# sort2own — install/uninstall/dist
#
# The tool is deliberately a single file plus an optional sidecar (see §1 of
# CLAUDE.md), so there is nothing to compile. This exists so the release is
# installed the same way every time instead of being copied by hand, and so
# the RPM spec has one place to call.
#
#   make install                 → /usr/share/sort2own + /usr/bin/sort2own
#   make install PREFIX=~/.local → a home install, no root needed
#   make DESTDIR=/tmp/stage install
#   make dist                    → sort2own-<version>.tar.gz from HEAD
#   make check                   → the test suite

NAME    := sort2own

# The script is the one place a version is *written*: it has to work after a
# plain scp, with no build step to fill anything in, and it stamps its own
# version into every manifest it writes. So the tag and the spec follow it,
# never the other way round.
#
# rpm needs Version: as a literal, so the spec cannot read this. Instead
# check-version compares the two and `make dist` refuses on a mismatch, which
# is what keeps the tarball and --version honest. `make release V=x.y.z` is
# the one command that writes both.
VERSION      := $(shell sed -n 's/^__version__ = "\(.*\)"$$/\1/p' sort2own.py)
SPEC_VERSION := $(shell sed -n 's/^Version:[[:space:]]*//p' $(NAME).spec)

PREFIX  ?= /usr
BINDIR  ?= $(PREFIX)/bin
DATADIR ?= $(PREFIX)/share
# Arch-independent, so /usr/share and never /usr/lib64 — an RPM built from
# this is noarch, and %{_libdir} would resolve wrongly on x86_64.
APPDIR  ?= $(DATADIR)/$(NAME)
DOCDIR  ?= $(DATADIR)/doc/$(NAME)
LICDIR  ?= $(DATADIR)/licenses/$(NAME)

INSTALL ?= install
PYTHON  ?= python3

.PHONY: all check check-version install uninstall dist release clean version

all:
	@echo "$(NAME) $(VERSION) — nothing to build; try 'make install' or 'make dist'"

version:
	@echo $(VERSION)

check-version:
	@test "$(VERSION)" = "$(SPEC_VERSION)" || { \
	  echo "version mismatch: sort2own.py says $(VERSION), $(NAME).spec says $(SPEC_VERSION)"; \
	  echo "use 'make release V=x.y.z' to set both"; \
	  exit 1; }

check:
	$(PYTHON) -m pytest -q

# sort2own.py and hints_ofdb.py must land in the same directory: the sidecar is
# imported by bare name, and Python resolves a symlink before setting
# sys.path[0], so /usr/bin/sort2own -> $(APPDIR)/sort2own.py still finds it.
install:
	$(INSTALL) -d $(DESTDIR)$(APPDIR)
	$(INSTALL) -m 0755 sort2own.py   $(DESTDIR)$(APPDIR)/sort2own.py
	$(INSTALL) -m 0644 hints_ofdb.py $(DESTDIR)$(APPDIR)/hints_ofdb.py
	$(INSTALL) -d $(DESTDIR)$(BINDIR)
	ln -sf $(APPDIR)/sort2own.py $(DESTDIR)$(BINDIR)/$(NAME)
	$(INSTALL) -Dm 0644 README.md $(DESTDIR)$(DOCDIR)/README.md
	$(INSTALL) -Dm 0644 LICENSE   $(DESTDIR)$(LICDIR)/LICENSE

uninstall:
	rm -f  $(DESTDIR)$(BINDIR)/$(NAME)
	rm -f  $(DESTDIR)$(APPDIR)/sort2own.py $(DESTDIR)$(APPDIR)/hints_ofdb.py
	rm -f  $(DESTDIR)$(DOCDIR)/README.md $(DESTDIR)$(LICDIR)/LICENSE
	-rmdir $(DESTDIR)$(APPDIR) $(DESTDIR)$(DOCDIR) $(DESTDIR)$(LICDIR)

# From HEAD, not the working tree, so a release tarball always matches a commit
# — and so an untracked file cannot end up in a release by accident.
dist: check-version
	git archive --format=tar.gz --prefix=$(NAME)-$(VERSION)/ \
		-o $(NAME)-$(VERSION).tar.gz HEAD
	@echo "wrote $(NAME)-$(VERSION).tar.gz"

# The only thing that writes a version. Leaves the %changelog entry to you:
# it needs prose, and a generated one would say nothing worth reading.
release:
	@test -n "$(V)" || { echo "usage: make release V=1.1.0"; exit 1; }
	@echo "$(V)" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$$' || \
	  { echo "V must look like 1.1.0 (got '$(V)')"; exit 1; }
	sed -i 's/^__version__ = ".*"/__version__ = "$(V)"/' sort2own.py
	sed -i 's/^Version:.*/Version:        $(V)/' $(NAME).spec
	sed -i 's/^Release:.*/Release:        1%{?dist}/' $(NAME).spec
	$(MAKE) check
	@echo
	@echo "sort2own.py and $(NAME).spec now say $(V)."
	@echo "Add a %changelog entry, then:"
	@echo "    git commit -a -m 'Release $(V)' && git tag -a v$(V) -m 'sort2own $(V)'"

clean:
	rm -f $(NAME)-*.tar.gz
	rm -rf __pycache__ tests/__pycache__ .pytest_cache
