#!/usr/bin/env bash

docker stop dallebot
docker rm dallebot
docker run --restart always --name dallebot -d -v dallebot_volume:/dallebot/dallebot/logs dallebot/dallebot bash -c "cd /dallebot/dallebot && python3 -m dallebot"
docker logs -f dallebot