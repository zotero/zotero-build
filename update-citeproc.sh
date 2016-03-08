#!/bin/sh
ZOTEROSRC="../zotero"
if [ -e "./config.sh" ]; then
	. ./config.sh
fi

outFile="$ZOTEROSRC/chrome/content/zotero/xpcom/citeproc.js"

if [ ! -e "$outFile" ]; then
	>&2 echo "$outFile not found. Looking for Zotero source in $(readlink -f $ZOTEROSRC)"
	exit 78 # EX_CONFIG: configuration error (from sysexits.h)
fi

curl https://raw.githubusercontent.com/Juris-M/citeproc-js/master/LICENSE > "$outFile"
echo >> "$outFile"
curl https://raw.githubusercontent.com/Juris-M/citeproc-js/master/citeproc.js >> "$outFile"