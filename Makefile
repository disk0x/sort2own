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

# The script is the one place a version is written; everything else reads it
# from here, so the spec and the tarball can never disagree with --version.
VERSION := $(shell sed -n 's/^__version__ = "\(.*\)"$$/\1/p' sort2own.py)

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

.PHONY: all check install uninstall dist clean version

all:
	@echo "$(NAME) $(VERSION) — nothing to build; try 'make install' or 'make dist'"

version:
	@echo $(VERSION)

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

# From HEAD, not the working tree, so a release tarball always matches a commit.
dist:
	git archive --format=tar.gz --prefix=$(NAME)-$(VERSION)/ \
		-o $(NAME)-$(VERSION).tar.gz HEAD
	@echo "wrote $(NAME)-$(VERSION).tar.gz"

clean:
	rm -f $(NAME)-*.tar.gz
	rm -rf __pycache__ tests/__pycache__ .pytest_cache
