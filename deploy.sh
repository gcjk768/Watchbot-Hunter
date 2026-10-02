#!/bin/sh
# Copy the committed code to the NAS and rebuild. .env and data/ on the NAS are never overwritten.
set -e
NAS="${NAS:-<OWNER>@<LAN_IP>}"
DIR=/volume1/docker/watchbot
# made here as the owner (uid 1000): if Docker created it for the bind mount it would be owned by root
VAULT="/volume1/<USER>/Obsidian/Watchbot"
git archive HEAD | ssh "$NAS" "mkdir -p '$VAULT' $DIR/data && cd $DIR && tar xf - --exclude=data && docker compose up -d --build"
