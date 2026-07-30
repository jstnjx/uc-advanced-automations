.PHONY: test build-wheel build-remote clean

test:
	python -m compileall -q src tests driver.py
	PYTHONPATH=src python -m unittest discover -s tests -v
	node --check src/uc_advanced_automations/static/app.js

build-wheel:
	python -m pip wheel . --no-deps -w dist

# Run this on ARM64 Linux, or inside the official Unfolded Circle ARM64
# PyInstaller container. The output is directly installable with Install custom.
build-remote:
	./tools/build_remote.sh aarch64

clean:
	rm -rf build artifacts dist *.spec *.tar.gz
