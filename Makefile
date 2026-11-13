SHELL := /bin/bash
ROOT := $(shell pwd)
APP := dist/DS 必应壁纸.app

.PHONY: all build icon helper serve run app install agent uninstall-agent uninstall-bing test clean logs status

all: build

build: helper icon
	./scripts/build-app.sh

helper:
	./scripts/build-setwall.sh

icon:
	./scripts/build-icon.sh

# Foreground server with the browser opening on start.
serve:
	./scripts/start-server.sh

run: serve

app: build
	open "$(APP)"

install:
	./scripts/install-app.sh

agent:
	./scripts/install-agent.sh

uninstall-agent:
	./scripts/uninstall-agent.sh

uninstall-bing:
	./scripts/uninstall-bing-wallpaper.sh

test:
	/usr/bin/python3 -m unittest discover -s tests -t . -v

status:
	@/usr/bin/curl -fsS "http://127.0.0.1:$${DSBW_PORT:-8791}/api/health" || echo "server not running"

logs:
	tail -n 40 "$(HOME)/Library/Application Support/DSBingWallpaper/app.log"

clean:
	rm -rf dist AppIcon.icns AppIcon.iconset .runtime/modulecache .runtime/icon
