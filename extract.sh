#!/bin/bash

CHUNK_START="---CHUNK_S-838974a7-0088-42da-b3e0-de74d6b8d23d"
CHUNK_DONE="---CHUNK_E-838974a7-0088-42da-b3e0-de74d6b8d23d"
END="---END-838974a7-0088-42da-b3e0-de74d6b8d23d"
START="---START-838974a7-0088-42da-b3e0-de74d6b8d23d"
START_END="---START_E-838974a7-0088-42da-b3e0-de74d6b8d23d"
ERROR_START="---ERROR_S-838974a7-0088-42da-b3e0-de74d6b8d23d"
ERROR_END="---ERROR_E-838974a7-0088-42da-b3e0-de74d6b8d23d"
STOP="StopExtract"

CHUNKSIZE=3072

error() {
  msg=$1

  clear
  echo "$ERROR_START"
  echo "$msg"
  echo "$ERROR_END"
  exit 1
}

start () {
  hash=$1

  clear
  echo "$START"
  echo "$hash"
  echo "$START_END"
}

exfil () {
  f=$1
  c=$2

  clear

  echo "$CHUNK_START-$c"
  base64 -w 0 $f
  echo -e "\n$CHUNK_DONE-$c"
}

# Handshake
read -p "Enter file:" -r file
read -p "Enter chunk start:" -r start

if [[ ! -f $file ]]; then
  error "File does not exist"
fi

cksum=$(sha256sum $file | cut -d' ' -f1)
start $cksum

state=LAUNCHED
while read -r line; do
  if [[ $line == $STOP ]]; then
    state=END
  fi

  case $state in
    LAUNCHED)
      c=$start
      filesize=$(stat --printf='%s' "$file")
      state=CONTENT
    ;&
    CONTENT)
      offset=$((1 + ($CHUNKSIZE * $c)))

      # Last chunk will be empty
      tail -c "+$offset" $file | head -c $CHUNKSIZE > chunk.bin
      exfil chunk.bin $c

      if (( $offset > $filesize )); then
        state=END
      fi
      
      c=$(($c + 1))
    ;;
    END)
      rm chunk.bin
      clear
      echo "$END"
      exit 0
    ;;
  esac
done