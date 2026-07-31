.PHONY: test build-wheel build-remote verify-remote clean

test:
	python -m compileall -q src tests driver.py
	PYTHONPATH=src python -m unittest discover -s tests -v
	@for file in src/uc_advanced_automations/static/*.js; do node --check "$$file"; done

build-wheel:
	python -m pip wheel . --no-deps --no-build-isolation -w dist

# Run this on ARM64 Linux, or inside the official Unfolded Circle ARM64
# PyInstaller container. The output is directly installable with Install custom.
build-remote:
	bash ./tools/build_remote.sh aarch64

verify-remote:
	@test -n "$(ARCHIVE)" || (echo "Usage: make verify-remote ARCHIVE=uc-intg-...tar.gz" && exit 2)
	bash ./tools/verify_remote_archive.sh "$(ARCHIVE)"

clean:
	rm -rf build artifacts dist *.spec *.tar.gz
