import io
import re
import enum
import time 
import base64
import asyncio
import hashlib
import argparse
import traceback
import functools

from websockets.client import connect
from websockets.exceptions import ConnectionClosed

VERBOSITY=1

START="---START-838974a7-0088-42da-b3e0-de74d6b8d23d"
START_END="---START_E-838974a7-0088-42da-b3e0-de74d6b8d23d"
CHUNK_START = "---CHUNK_S-838974a7-0088-42da-b3e0-de74d6b8d23d"
CHUNK_END = "---CHUNK_E-838974a7-0088-42da-b3e0-de74d6b8d23d"
ERROR_START="---ERROR_S-838974a7-0088-42da-b3e0-de74d6b8d23d"
ERROR_END="---ERROR_E-838974a7-0088-42da-b3e0-de74d6b8d23d"
END="---END-838974a7-0088-42da-b3e0-de74d6b8d23d"
STOP='StopExtract'

class LogStatus(enum.Enum):
    OK = 'ok'
    BAD = 'bad'
    OUT = 'out'
    IN = 'in'


LOK = LogStatus.OK
LBAD = LogStatus.BAD
LOUT = LogStatus.OUT
LIN = LogStatus.IN


def log(status, msg):
    match status:
        case LogStatus.OK:
            prefix = '\033[92m[+]\033[0m '
        case LogStatus.BAD:
            prefix = '\033[91m[-]\033[0m '
        case LogStatus.OUT:
            prefix = '\033[96m[>]\033[0m '
        case LogStatus.IN:
            prefix = '\033[95m[<]\033[0m '
        case _:
            prefix = ''

    print(f'{prefix}{msg}')


def ws_log(content, outgoing):
    if VERBOSITY == 0:
        return
    
    # Filtered out some instructions in low verbosity as there's a lot of
    # sync/ack noise - although be aware that interesting instructions can 
    # be received in the same frame as a sync

    if VERBOSITY >= 2:
        msg = content
    elif content.startswith('4.sync'):
        return
    elif content.startswith('3.ack'):
        return
    else:
        suffix = '' if len(content) < 100 else ' [...]'
        msg = content[:100] + suffix

    if outgoing:
        log(LOUT, msg)
    else:
        log(LIN, msg)


async def ws_send(ws, data):
    ws_log(data, outgoing=True)
    await ws.send(data)


async def ws_recv(ws):
    data = await ws.recv()
    ws_log(data, outgoing=False)
    return guac_parse_msg(data)


class GuacInstruction:
    """Guac instructions as described in the protocol reference"""

    def __init__(self, args):
        self.args = args

    @property
    def name(self):
        return self.args[0]
    
    def __repr__(self):
        return '<GuacInstruction name=%r, args=%r>' % (self.name, self.args)


class GuacMsg:
    """A guacamole message composed of one or more instructions"""

    def __init__(self, instructions):
        self.instructions = instructions

    def __repr__(self):
        return '<GuacMsg instructions=%r>' % self.instructions


def guac_parse_arg(text, acc):
    i = text.find('.')

    assert i != -1, \
        'Invalid argument format: %r' % text

    l = int(text[:i])
    s = text[i+1:i+1+l]

    acc.append(s)
    return text[i+1+l:]


def guac_parse_instruction(text, acc):
    args = []
    while True:
        text = guac_parse_arg(text, args)

        if text.startswith(','):
            text = text[1:]

        if text.startswith(';'):
            break

    acc.append(GuacInstruction(args))
    return text[1:]


def guac_parse_msg(text):
    msg = GuacMsg([])
    
    while text := guac_parse_instruction(text, msg.instructions):
        pass

    return msg


class GuacClient:
    """GuacClient - composed of the `run` task which receives inbound messages,
    `stream` which consumes them, and utilities to send Guacamole instructions
    
    """

    def __init__(self, ws):
        self.ws = ws
        self.out = asyncio.Queue(maxsize=0x4000)
        self.last_sync = 0
        self.streamno = -1

    async def run(self):
        """Receive messages and add them to the queue"""

        while True:
            try:
                msg = await ws_recv(self.ws)
            except ConnectionClosed:
                return

            for instr in msg.instructions:
                if instr.name == 'sync':
                    await self._sync(instr.args[1])
                
                if instr.name == 'blob':
                    await self._ack(instr.args[1])

                await self.out.put(instr)

    async def _sync(self, ts):
        await self.send_args('sync', ts)

    async def _ack(self, stream):
        await self.send_args('ack', stream, 'OK', 0)

    async def stream(self):
        """Stream and consume instructions from the queue"""
    
        while True:
            instr = await self.out.get()
            yield instr

    async def send_args(self, *args):
        assert len(args) >= 1, 'Cannot send empty arguments' 
        args = [str(x) for x in args]
        args = zip((len(x) for x in args), args)
        await ws_send(self.ws, ','.join('%d.%s' % x for x in args) + ';')

    async def send_keypress(self, x11_code):
        await self.send_args('key', x11_code, 1)
        await self.send_args('key', x11_code, 0)

    async def send_newline(self):
        await self.send_keypress(65293)  # Carriage return

    async def send_size(self, width, height):
        await self.send_args('size', width, height)

    async def send_mouse(self, x, y, mask):
        await self.send_args('mouse', x, y, mask)

    async def send_selection(self, from_x, from_y, to_x, to_y):
        """Emulates dragging mouse selection between coords"""

        await self.send_mouse(from_x, from_y, 1)
        await self.send_mouse(to_x, to_y, 1)
        await self.send_mouse(to_x, to_y, 0)
    
    async def send_line(self, text):
        for key in text.encode('ascii'):
            await self.send_keypress(key)

        await self.send_newline()


class ClipboardProtocolError(Exception):
    pass


class RetriesExceededError(Exception):
    pass


def retryable(retry_count):
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            retries = 0
            while True:
                try:
                    return await fn(self, *args, **kwargs)
                except ClipboardProtocolError as e:
                    log(LBAD, f'Clipboard protocol error: retry_count={retries}')
                    retries += 1
                    if retries > retry_count:
                        raise RetriesExceededError from e
        return wrapper
    return decorator


async def recv_clipboard(client, timeout=5):
    t = time.time()

    # We receive a `clipboard` event followed by one or more `blob`, then
    # an `end`

    async for instruction in client.stream():
        if time.time() - t > timeout:
            raise ClipboardProtocolError("Didn't see clipboard")
        
        if instruction.name == 'clipboard':
            streamno = instruction.args[1]
            break
    
    value = io.BytesIO()

    async for instruction in client.stream():
        if time.time() - t > timeout:
            raise ClipboardProtocolError("Saw clipboard but didn't see end")

        if instruction.name == 'blob' and instruction.args[1] == streamno:
            value.write(base64.b64decode(instruction.args[2]))
        elif instruction.name == 'end' and instruction.args[1] == streamno:
            break

    return value.getvalue().decode('utf-8')


class ExtractorState(enum.Enum):
    BEGIN = 'BEGIN'
    LAUNCHED = 'LAUNCHED'
    AWAITING_DATA = 'AWAITING_DATA'
    RECEIVED_DATA = 'RECEIVED_DATA'
    END = 'END'
    ERROR = 'ERROR'


class ClipboardExtractor:
    _i = 0
    _state = ExtractorState.BEGIN
    _file_hash = ''

    def __init__(self, client, outfile, remote_file):
        self.client = client
        self.out = open(outfile, 'w+b')
        self.remote_file = remote_file

    async def run(self):
        while True:
            log(LOK, f'Processing {self._state}')
            fn = getattr(self, 'handle_%s' % self._state.value)
            if not (await fn()):
                break

    async def handle_BEGIN(self):
        raise NotImplementedError
    
    async def handle_AWAITING_DATA(self):
        raise NotImplementedError
    
    async def handle_LAUNCHED(self):
        raise NotImplementedError

    async def handle_RECEIVED_DATA(self):
        raise NotImplementedError

    async def handle_ERROR(self):
        raise NotImplementedError 

    async def handle_END(self):
        self.out.seek(0)
        file_hash = hashlib.file_digest(self.out, 'sha256').hexdigest()

        assert file_hash.lower() == self._file_hash.lower(), \
            'File hashes do not match: local=%r, remote=%r' % (file_hash, self._file_hash)
        
        log(LOK, f'File hashes match: {file_hash}')
        
        self.out.close() 
        return False
    
    def process_start(self, start_chunk):
        lines = start_chunk.splitlines()

        assert len(lines) >= 3, \
            'Malformed START envelope: %r' % start_chunk
        assert lines[0] == START, \
            'Did not see START: %r' % start_chunk
        assert lines[2] == START_END, \
            'Did not see START_END %r' % start_chunk
        assert len(lines[1]) == 64, \
            'File hash does not look like SHA256: %r' % lines[1]

        self._file_hash = lines[1]
        log(LOK, 'Received file hash: %s' % self._file_hash)
    

class SSHClipboardExtractor(ClipboardExtractor):
    WIDTH = 2048
    HEIGHT = 2048
    DEFAULT_SCRIPT = '$HOME/extract.sh'

    def __init__(self, *args, script=None, **kwargs):
        super().__init__(*args, **kwargs)
    
        if script:
            self.script = script
        else:
            self.script = self.DEFAULT_SCRIPT

    async def handle_BEGIN(self):
        await asyncio.sleep(2)
        await self.client.send_size(self.WIDTH, self.HEIGHT)
        await self.client.send_line(self.script)
        self._state = ExtractorState.LAUNCHED
        return True
    
    async def handle_LAUNCHED(self):
        await asyncio.sleep(2)
        await self.client.send_line(self.remote_file)
        await self.client.send_line(str(self._i))
        
        await self._get_start_chunk()
        self._state = ExtractorState.RECEIVED_DATA
        return True

    async def handle_RECEIVED_DATA(self):
        await self.client.send_newline()
        self._state = ExtractorState.AWAITING_DATA
        return True

    async def handle_AWAITING_DATA(self):
        try:
            return await self._get_next_chunk()
        except RetriesExceededError as e:
            traceback.print_exception(e)
            log(LBAD, 'Retries exceeded, restarting script')
            self._state = ExtractorState.ERROR
            return True
        
    async def handle_ERROR(self):
        # On error we exit the script and start again at the last chunk
        await asyncio.sleep(1)
        await self.client.send_line(STOP)
        await self._get_end_chunk()
        self._state = ExtractorState.BEGIN
        return True
        
    @retryable(retry_count=5)
    async def _get_next_chunk(self):
        # There is commanality between SSH & RDP but the semantics are quite different.
        # There can be delays in the terminal text being updated, so receiving out of
        # date chunks is expected and we should re-request the clipboard in that case. 
        # The emulated terminal also formats our clipboard data into fixed-width lines.

        chunk = await self._get_clipboard()

        s = -1
        e = -1
    
        lines = chunk.split('\n')
        for i, line in enumerate(lines):
            if line.startswith(CHUNK_START):
                if line != f'{CHUNK_START}-{self._i}':
                    msg = f'CHUNK_START seen but expected index {self._i}: {line}'
                    raise ClipboardProtocolError(msg)
                s = i
            elif line.startswith(CHUNK_END):
                if line != f'{CHUNK_END}-{self._i}':
                    msg = f'CHUNK_END seen but expected index {self._i}: {line}'
                    raise ClipboardProtocolError(msg)             
                e = i
            elif line.startswith(END):
                self._state = ExtractorState.END
                return True
            
        if s == -1:
            raise ClipboardProtocolError('Did not find CHUNK_START: %r' % chunk)

        if e == -1:
            raise ClipboardProtocolError('Did not find CHUNK_END: %r' % chunk)

        self._i += 1
        self.out.write(base64.b64decode(''.join(lines[s+1:e])))
        self._state = ExtractorState.RECEIVED_DATA
        return True
    
    @retryable(retry_count=5)
    async def _get_end_chunk(self):
        chunk = await self._get_clipboard()
    
        if chunk.startswith(END):
            return
        
        raise ClipboardProtocolError('Did not see END')
    
    @retryable(retry_count=5)
    async def _get_start_chunk(self):
        chunk = await self._get_clipboard()

        if chunk.startswith(START):
            return self.process_start(chunk)
        
        raise ClipboardProtocolError('Did not see START')

    async def _get_clipboard(self):
        await self.client.send_selection(0, 0, self.WIDTH, self.HEIGHT)
        chunk = await recv_clipboard(self.client)
        return chunk

class RDPClipboardExtractor(ClipboardExtractor):
    DEFAULT_SCRIPT = 'C:\\Users\\User\\extract.ps1'

    def __init__(self, *args, script=None, **kwargs):
        super().__init__(*args, **kwargs)

        if script:
            self.script = script
        else:
            self.script = self.DEFAULT_SCRIPT

    async def handle_BEGIN(self):
        await asyncio.sleep(5)
        await self.client.send_line(self.script)
        self._state = ExtractorState.LAUNCHED
        return True
    
    async def handle_LAUNCHED(self):
        await asyncio.sleep(5)
        await self.client.send_line(self.remote_file)
        await self.client.send_line(str(self._i))
        
        while True:
            chunk = await recv_clipboard(self.client)
            if chunk.startswith(START):
                self.process_start(chunk)
                break

        self._state = ExtractorState.RECEIVED_DATA
        return True

    async def handle_RECEIVED_DATA(self):
        await self.client.send_newline()
        self._state = ExtractorState.AWAITING_DATA
        return True

    async def handle_AWAITING_DATA(self):
        try:
            return await self._get_next_chunk()
        except ClipboardProtocolError as e:
            traceback.print_exception(e)
            log(LBAD, 'Failed to retrieve chunk, restarting script')
            self._state = ExtractorState.ERROR
            return True

    async def handle_ERROR(self):
        # On error we exit the script and start again at the last chunk
        await asyncio.sleep(1)
        await self.client.send_line(STOP)

        while True:
            chunk = await recv_clipboard(self.client)
            if chunk.startswith(END):
                break

        self._state = ExtractorState.BEGIN
        return True

    async def _get_next_chunk(self):
        # For some reason guacd sends duplicate clipboard events so we skip 
        # any chunks we've already seen, however it is likely that the chunk
        # we requested has also been transmitted

        # Compared to SSH, we can be stricter with the assertions here since we're
        # in full control of the clibpoard - i.e. there's nothing on guad's side 
        # which is reformatting our data such as the terminal emulator for SSH

        while True:
            chunk = await recv_clipboard(self.client)

            if chunk.startswith(START):
                continue

            if chunk.startswith(END):
                self._state = ExtractorState.END
                return True
                        
            lines = chunk.splitlines()

            assert len(lines) == 3, \
                'Malformed chunk: %r' % chunk
            assert lines[0].startswith(CHUNK_START), \
                'Missing CHUNK_START: %r' % chunk
            assert lines[2].startswith(CHUNK_END), \
                'Missing CHUNK_END: %r' % chunk
            
            count = self._extract_chunknum(lines[0])

            if count < self._i:
                log(LBAD, f'Saw duplicate of chunk {count}')
                continue

            assert count == self._i, \
                'Expected chunk %d but received %d' % (self._i, count)
            
            self.out.write(base64.b64decode(lines[1]))

            self._i += 1
            self._state = ExtractorState.RECEIVED_DATA
            return True

    def _extract_chunknum(self, header):
        match = re.match(f'{CHUNK_START}-(\d+)$', header)
        assert match, 'Malformed CHUNK_START: %r' % header
        return int(match.group(1))


async def main(*, url, extract, platform, outfile, script):
    url = url.replace('https://', 'wss://')
    url = url.replace('http://', 'ws://')

    async with connect(url) as ws:
        client = GuacClient(ws)

        loop = asyncio.get_running_loop()
        loop.create_task(client.run(), name='guac-client')

        if platform == 'windows-rdp':
            extractor = RDPClipboardExtractor(client, outfile, extract, script=script)
        elif platform == 'linux-ssh':
            extractor = SSHClipboardExtractor(client, outfile, extract, script=script)
        else:
            raise NotImplementedError(f'Unsupported platform: {platform}')

        log(LOK, 'Waiting 10 seconds for launch...')
        await asyncio.sleep(10)
        await extractor.run()
    
    log(LOK, f'Done - your file is at {outfile}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-e', '--extract', required=True, help='The remote file to extract')
    parser.add_argument('-u', '--url', required=True, help='Websocket tunnel url')
    parser.add_argument('-p', '--platform', required=True, choices=('windows-rdp', 'linux-ssh',))
    parser.add_argument('-o', '--outfile', default='output.bin')
    parser.add_argument('-s', '--script', help='Path to the script on the remote')

    args = parser.parse_args()

    asyncio.run(main(
        url=args.url, 
        extract=args.extract.strip(), 
        platform=args.platform, 
        outfile=args.outfile,
        script=args.script,
    ))
