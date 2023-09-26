#!/usr/bin/env bash

git pull
docker build -t dallebot/dallebot -f Dockerfile-raspberry-pi .
