Install dependencies on python 3.11+:


```
pip install -r requirements.txt
```

Example usage for RDP:

- Upload the `extract.ps1` script to a location on disk
- Connect to the RDP session via the guacamole client and copy/paste the `/websocket-tunnel` url
  - Easiest way to do this is via a proxy like ZAP/Burp Suite or the network tab in browser developer tools
- Open powershell
- Run the following script


```
#!/bin/bash

url='http://localhost:8080/guacamole/websocket-tunnel?token=EA9DA37F6E845173D16CD07F7DF0F51D5E865BC3FA581BBEB8724CB3DCE983F6&GUAC_DATA_SOURCE=jwt&GUAC_ID=Desktop&GUAC_TYPE=c&GUAC_WIDTH=2560&GUAC_HEIGHT=1292&GUAC_DPI=96&GUAC_TIMEZONE=Europe%2FLondon&GUAC_AUDIO=audio%2FL8&GUAC_AUDIO=audio%2FL16&GUAC_IMAGE=image%2Fjpeg&GUAC_IMAGE=image%2Fpng&GUAC_IMAGE=image%2Fwebp'

mkdir out
python3 extract.py -u $url -s "C:\\Users\\John Doe\\extract.ps1" -e "C:\\extract.zip" -o out/tools.zip --platform windows-rdp

```

For SSH:

- Upload the `extract.sh` script somewhere
- Make it executable
- Connect to the SSH server 
- Run the script 


```
python3 extract.py -u $url -s "/home/jdoe/extract.zip" -e "/home/jdoe/extract.zip" -o out/tools.zip --platform linux-ssh
```
