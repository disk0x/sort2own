# RPM spec for sort2own.
#
# Build with:
#     make dist
#     rpmbuild -ta sort2own-*.tar.gz
# or, if you keep a ~/rpmbuild tree:
#     make dist && cp sort2own-*.tar.gz ~/rpmbuild/SOURCES/
#     rpmbuild -ba sort2own.spec
#
# The test suite needs python3-pytest. To build without it:
#     rpmbuild -ba --without check sort2own.spec
%bcond_without check

# The package installs a plain script, not an importable module, so there is
# nothing for Fedora's automatic byte-compilation to do outside sitelib. Say
# so explicitly: otherwise a generated __pycache__ under the data directory
# would show up as an unpackaged file and fail the build.
# (Macro names are spelled out in words in these comments on purpose — rpm
# expands macros inside comments too, and warns each time it does.)
%global _python_bytecompile_extra 0

Name:           sort2own
Version:        1.0.0
Release:        2%{?dist}
Summary:        Sort MakeMKV disc rips into a Jellyfin-ready library

License:        AGPL-3.0-only
URL:            https://github.com/disk0x/sort2own
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  make
# python3, not python3-devel: nothing here is compiled or installed into
# sitelib, so the python rpm macros are not needed and pulling in the devel
# stack for a single script would be gratuitous.
BuildRequires:  python3 >= 3.11
%if %{with check}
BuildRequires:  python3-pytest
%endif

# 3.11 is the floor: the config loader uses stdlib tomllib.
Requires:       python3 >= 3.11
# A file dependency rather than "Requires: ffmpeg" on purpose. Full ffmpeg
# lives in RPM Fusion, but Fedora's own ffmpeg-free also ships ffprobe, and
# ffprobe is all this needs — a file dep is satisfied by either.
Requires:       /usr/bin/ffprobe

%description
MakeMKV rips a disc into Title_t00.mkv, Title_t01.mkv … with no indication of
which title is the feature and which are trailers, menu loops or featurettes.
Dropped into Jellyfin, every title is scanned as a separate movie.

sort2own works out which title is the main feature, which are alternate cuts
and which are extras, then lays them out the way Jellyfin expects: one main
file, alternate cuts as versions, extras in typed subfolders, TV episodes in
season folders. It is non-destructive — it hardlinks by default, never
overwrites a destination, never modifies the source, and records every run in
a manifest that %{name} --undo can reverse.

%prep
%autosetup -n %{name}-%{version}

%build
# Nothing to compile — a single script plus an optional sidecar.

%install
%make_install PREFIX=%{_prefix}

%if %{with check}
%check
# Runs without ffmpeg present: the fixtures that need it skip themselves, so
# this passes in a clean mock chroot. Plain python3 rather than the python
# rpm macro, which only exists when python3-devel is installed.
python3 -m pytest -q
%endif

%files
# Absolute paths, because the Makefile has already put these in the buildroot;
# the relative "%%license LICENSE" form would install a second copy and leave
# the Makefile's as an unpackaged file. The %%dir lines keep every directory
# this package creates owned by it.
%dir %{_datadir}/licenses/%{name}
%license %{_datadir}/licenses/%{name}/LICENSE
%dir %{_datadir}/doc/%{name}
%doc %{_datadir}/doc/%{name}/README.md
%{_bindir}/%{name}
%dir %{_datadir}/%{name}
%{_datadir}/%{name}/sort2own.py
%{_datadir}/%{name}/hints_ofdb.py

%changelog
* Thu Sep 10 2026 dkr <19468139+disk0x@users.noreply.github.com> - 1.0.0-2
- Drop the python3-devel build dependency; nothing here needs the python rpm
  macros, and %%{python3} in %%check was the only thing that did.
- Make the test suite optional: build with --without check to skip it and its
  python3-pytest dependency.
- Stop rpm warning about macros expanded inside comments.

* Thu Sep 10 2026 dkr <19468139+disk0x@users.noreply.github.com> - 1.0.0-1
- First packaged release.
- Adds __version__, a --version flag, and a version field in each manifest run.
- Removes code that nothing read, including the last remnant of the withdrawn
  shape-based extras heuristic.
