# Advanced Automations v2.0.0
.PHONY: test test-public test-private build-wheel build-remote verify-remote clean

test: test-public
	@if [ -d tests ] && find tests -type f -name 'test_*.py' -print -quit | grep -q .; then \
		$(MAKE) test-private; \
	else \
		echo "Private tests not present; public validation completed."; \
	fi

test-public:
	python -m compileall -q src driver.py tools/public_smoke_test.py
	PYTHONPATH=src$${PYTHONPATH:+:$$PYTHONPATH} python tools/public_smoke_test.py
	@for file in src/uc_advanced_automations/static/*.js; do node --check "$$file"; done

test-private:
	PYTHONPATH=src$${PYTHONPATH:+:$$PYTHONPATH} python -m unittest discover -s tests -v

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
