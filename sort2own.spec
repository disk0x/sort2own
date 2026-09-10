# RPM spec for sort2own.
#
# Build with:
#     make dist
#     rpmbuild -ta sort2own-1.0.0.tar.gz
# or, if you keep a ~/rpmbuild tree:
#     make dist && cp sort2own-*.tar.gz ~/rpmbuild/SOURCES/
#     rpmbuild -ba sort2own.spec
#
# NOTE: this spec has not been built or rpmlint-ed — it was written in an
# environment with no rpm tooling. Expect to iterate on the first build.

# The package installs a plain script, not an importable module, so there is
# nothing for Fedora's automatic byte-compilation to do outside sitelib. Say
# so explicitly: otherwise a generated __pycache__ under %{_datadir} shows up
# as an unpackaged file and fails the build.
%global _python_bytecompile_extra 0

Name:           sort2own
Version:        1.0.0
Release:        1%{?dist}
Summary:        Sort MakeMKV disc rips into a Jellyfin-ready library

License:        AGPL-3.0-or-later
URL:            https://github.com/disk0x/sort2own
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  make
BuildRequires:  python3-devel >= 3.11
BuildRequires:  python3-pytest

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

%check
# Runs without ffmpeg present: the fixtures that need it skip themselves, so
# this passes in a clean mock chroot.
%{python3} -m pytest -q

%files
%license %{_datadir}/licenses/%{name}/LICENSE
%doc %{_datadir}/doc/%{name}/README.md
%{_bindir}/%{name}
%dir %{_datadir}/%{name}
%{_datadir}/%{name}/sort2own.py
%{_datadir}/%{name}/hints_ofdb.py

%changelog
* Thu Sep 10 2026 dkr <19468139+disk0x@users.noreply.github.com> - 1.0.0-1
- First packaged release.
- Adds __version__, a --version flag, and a version field in each manifest run.
- Removes code that nothing read, including the last remnant of the withdrawn
  shape-based extras heuristic.
