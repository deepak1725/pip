"""Generate and work with PEP 425 Compatibility Tags."""
from __future__ import absolute_import

import distutils.util
import logging
import platform
import re
import sys
import sysconfig

from pip._vendor.packaging.tags import (
    Tag,
    compatible_tags,
    cpython_tags,
    interpreter_name,
    interpreter_version,
    mac_platforms,
)

import pip._internal.utils.glibc
from pip._internal.utils.typing import MYPY_CHECK_RUNNING

if MYPY_CHECK_RUNNING:
    from typing import (
        Callable, Iterator, List, Optional, Set, Tuple, Union
    )

    from pip._vendor.packaging.tags import PythonVersion

logger = logging.getLogger(__name__)

_osx_arch_pat = re.compile(r'(.+)_(\d+)_(\d+)_(.+)')


def get_config_var(var):
    # type: (str) -> Optional[str]
    return sysconfig.get_config_var(var)


def version_info_to_nodot(version_info):
    # type: (Tuple[int, ...]) -> str
    # Only use up to the first two numbers.
    return ''.join(map(str, version_info[:2]))


def get_impl_version_info():
    # type: () -> Tuple[int, ...]
    """Return sys.version_info-like tuple for use in decrementing the minor
    version."""
    if interpreter_name() == 'pp':
        # as per https://github.com/pypa/pip/issues/2882
        # attrs exist only on pypy
        return (sys.version_info[0],
                sys.pypy_version_info.major,  # type: ignore
                sys.pypy_version_info.minor)  # type: ignore
    else:
        return sys.version_info[0], sys.version_info[1]


def get_flag(var, fallback, expected=True, warn=True):
    # type: (str, Callable[..., bool], Union[bool, int], bool) -> bool
    """Use a fallback method for determining SOABI flags if the needed config
    var is unset or unavailable."""
    val = get_config_var(var)
    if val is None:
        if warn:
            logger.debug("Config variable '%s' is unset, Python ABI tag may "
                         "be incorrect", var)
        return fallback()
    return val == expected


def get_abi_tag():
    # type: () -> Optional[str]
    """Return the ABI tag based on SOABI (if available) or emulate SOABI
    (CPython 2, PyPy)."""
    soabi = get_config_var('SOABI')
    impl = interpreter_name()
    abi = None  # type: Optional[str]

    if not soabi and impl in {'cp', 'pp'} and hasattr(sys, 'maxunicode'):
        d = ''
        m = ''
        u = ''
        is_cpython = (impl == 'cp')
        if get_flag(
                'Py_DEBUG', lambda: hasattr(sys, 'gettotalrefcount'),
                warn=is_cpython):
            d = 'd'
        if sys.version_info < (3, 8) and get_flag(
                'WITH_PYMALLOC', lambda: is_cpython, warn=is_cpython):
            m = 'm'
        if sys.version_info < (3, 3) and get_flag(
                'Py_UNICODE_SIZE', lambda: sys.maxunicode == 0x10ffff,
                expected=4, warn=is_cpython):
            u = 'u'
        abi = '%s%s%s%s%s' % (impl, interpreter_version(), d, m, u)
    elif soabi and soabi.startswith('cpython-'):
        abi = 'cp' + soabi.split('-')[1]
    elif soabi:
        abi = soabi.replace('.', '_').replace('-', '_')

    return abi


def _is_running_32bit():
    # type: () -> bool
    return sys.maxsize == 2147483647


def get_platform():
    # type: () -> str
    """Return our platform name 'win32', 'linux_x86_64'"""
    if sys.platform == 'darwin':
        # distutils.util.get_platform() returns the release based on the value
        # of MACOSX_DEPLOYMENT_TARGET on which Python was built, which may
        # be significantly older than the user's current machine.
        release, _, machine = platform.mac_ver()
        split_ver = release.split('.')

        if machine == "x86_64" and _is_running_32bit():
            machine = "i386"
        elif machine == "ppc64" and _is_running_32bit():
            machine = "ppc"

        return 'macosx_{}_{}_{}'.format(split_ver[0], split_ver[1], machine)

    # XXX remove distutils dependency
    result = distutils.util.get_platform().replace('.', '_').replace('-', '_')
    if result == "linux_x86_64" and _is_running_32bit():
        # 32 bit Python program (running on a 64 bit Linux): pip should only
        # install and run 32 bit compiled extensions in that case.
        result = "linux_i686"

    return result


def is_linux_armhf():
    # type: () -> bool
    if get_platform() != "linux_armv7l":
        return False
    # hard-float ABI can be detected from the ELF header of the running
    # process
    try:
        with open(sys.executable, 'rb') as f:
            elf_header_raw = f.read(40)  # read 40 first bytes of ELF header
    except (IOError, OSError, TypeError):
        return False
    if elf_header_raw is None or len(elf_header_raw) < 40:
        return False
    if isinstance(elf_header_raw, str):
        elf_header = [ord(c) for c in elf_header_raw]
    else:
        elf_header = [b for b in elf_header_raw]
    result = elf_header[0:4] == [0x7f, 0x45, 0x4c, 0x46]  # ELF magic number
    result &= elf_header[4:5] == [1]  # 32-bit ELF
    result &= elf_header[5:6] == [1]  # little-endian
    result &= elf_header[18:20] == [0x28, 0]  # ARM machine
    result &= elf_header[39:40] == [5]  # ARM EABIv5
    result &= (elf_header[37:38][0] & 4) == 4  # EF_ARM_ABI_FLOAT_HARD
    return result


def is_manylinux1_compatible():
    # type: () -> bool
    # Only Linux, and only x86-64 / i686
    if get_platform() not in {"linux_x86_64", "linux_i686"}:
        return False

    # Check for presence of _manylinux module
    try:
        import _manylinux
        return bool(_manylinux.manylinux1_compatible)
    except (ImportError, AttributeError):
        # Fall through to heuristic check below
        pass

    # Check glibc version. CentOS 5 uses glibc 2.5.
    return pip._internal.utils.glibc.have_compatible_glibc(2, 5)


def is_manylinux2010_compatible():
    # type: () -> bool
    # Only Linux, and only x86-64 / i686
    if get_platform() not in {"linux_x86_64", "linux_i686"}:
        return False

    # Check for presence of _manylinux module
    try:
        import _manylinux
        return bool(_manylinux.manylinux2010_compatible)
    except (ImportError, AttributeError):
        # Fall through to heuristic check below
        pass

    # Check glibc version. CentOS 6 uses glibc 2.12.
    return pip._internal.utils.glibc.have_compatible_glibc(2, 12)


def is_manylinux2014_compatible():
    # type: () -> bool
    # Only Linux, and only supported architectures
    platform = get_platform()
    if platform not in {"linux_x86_64", "linux_i686", "linux_aarch64",
                        "linux_armv7l", "linux_ppc64", "linux_ppc64le",
                        "linux_s390x"}:
        return False

    # check for hard-float ABI in case we're running linux_armv7l not to
    # install hard-float ABI wheel in a soft-float ABI environment
    if platform == "linux_armv7l" and not is_linux_armhf():
        return False

    # Check for presence of _manylinux module
    try:
        import _manylinux
        return bool(_manylinux.manylinux2014_compatible)
    except (ImportError, AttributeError):
        # Fall through to heuristic check below
        pass

    # Check glibc version. CentOS 7 uses glibc 2.17.
    return pip._internal.utils.glibc.have_compatible_glibc(2, 17)


def get_all_minor_versions_as_strings(version_info):
    # type: (Tuple[int, ...]) -> List[str]
    versions = []
    major = version_info[:-1]
    # Support all previous minor Python versions.
    for minor in range(version_info[-1], -1, -1):
        versions.append(''.join(map(str, major + (minor,))))
    return versions


def _mac_platforms(arch):
    # type: (str) -> List[str]
    match = _osx_arch_pat.match(arch)
    if match:
        name, major, minor, actual_arch = match.groups()
        mac_version = (int(major), int(minor))
        arches = [
            # Since we have always only checked that the platform starts
            # with "macosx", for backwards-compatibility we extract the
            # actual prefix provided by the user in case they provided
            # something like "macosxcustom_". It may be good to remove
            # this as undocumented or deprecate it in the future.
            '{}_{}'.format(name, arch[len('macosx_'):])
            for arch in mac_platforms(mac_version, actual_arch)
        ]
    else:
        # arch pattern didn't match (?!)
        arches = [arch]
    return arches


def _custom_manylinux_platforms(arch):
    # type: (str) -> List[str]
    arches = [arch]
    arch_prefix, arch_sep, arch_suffix = arch.partition('_')
    if arch_prefix == 'manylinux2014':
        # manylinux1/manylinux2010 wheels run on most manylinux2014 systems
        # with the exception of wheels depending on ncurses. PEP 599 states
        # manylinux1/manylinux2010 wheels should be considered
        # manylinux2014 wheels:
        # https://www.python.org/dev/peps/pep-0599/#backwards-compatibility-with-manylinux2010-wheels
        if arch_suffix in {'i686', 'x86_64'}:
            arches.append('manylinux2010' + arch_sep + arch_suffix)
            arches.append('manylinux1' + arch_sep + arch_suffix)
    elif arch_prefix == 'manylinux2010':
        # manylinux1 wheels run on most manylinux2010 systems with the
        # exception of wheels depending on ncurses. PEP 571 states
        # manylinux1 wheels should be considered manylinux2010 wheels:
        # https://www.python.org/dev/peps/pep-0571/#backwards-compatibility-with-manylinux1-wheels
        arches.append('manylinux1' + arch_sep + arch_suffix)
    return arches


def _get_custom_platforms(arch, platform):
    # type: (str, Optional[str]) -> List[str]
    arch_prefix, arch_sep, arch_suffix = arch.partition('_')
    if arch.startswith('macosx'):
        arches = _mac_platforms(arch)
    elif arch_prefix in ['manylinux2014', 'manylinux2010']:
        arches = _custom_manylinux_platforms(arch)
    elif platform is None:
        arches = []
        if is_manylinux2014_compatible():
            arches.append('manylinux2014' + arch_sep + arch_suffix)
        if is_manylinux2010_compatible():
            arches.append('manylinux2010' + arch_sep + arch_suffix)
        if is_manylinux1_compatible():
            arches.append('manylinux1' + arch_sep + arch_suffix)
        arches.append(arch)
    else:
        arches = [arch]
    return arches


def _get_python_version(version):
    # type: (str) -> PythonVersion
    if len(version) > 1:
        return int(version[0]), int(version[1:])
    else:
        return (int(version[0]),)


def _get_custom_interpreter(implementation=None, version=None):
    # type: (Optional[str], Optional[str]) -> str
    if implementation is None:
        implementation = interpreter_name()
    if version is None:
        version = interpreter_version()
    return "{}{}".format(implementation, version)


def _generic_tags(
    version=None,  # type: Optional[str]
    platform=None,  # type: Optional[str]
    impl=None,  # type: Optional[str]
    abi=None,  # type: Optional[str]
):
    # type: (...) -> List[Tuple[str, str, str]]
    supported = []  # type: List[Tuple[str, str, str]]

    # Versions must be given with respect to the preference
    if version is None:
        version_info = get_impl_version_info()
        versions = get_all_minor_versions_as_strings(version_info)
    else:
        versions = [version]
    current_version = versions[0]

    impl = impl or interpreter_name()

    abis = []  # type: List[str]

    abi = abi or get_abi_tag()
    if abi:
        abis[0:0] = [abi]

    abis.append('none')

    arches = _get_custom_platforms(platform or get_platform(), platform)

    # Current version, current API (built specifically for our Python):
    for abi in abis:
        for arch in arches:
            supported.append(('%s%s' % (impl, current_version), abi, arch))

    return supported


def _stable_unique_tags(tags):
    # type: (List[Tag]) -> Iterator[Tag]
    observed = set()  # type: Set[Tag]
    for tag in tags:
        if tag not in observed:
            observed.add(tag)
            yield tag


def get_supported(
    version=None,  # type: Optional[str]
    platform=None,  # type: Optional[str]
    impl=None,  # type: Optional[str]
    abi=None  # type: Optional[str]
):
    # type: (...) -> List[Tag]
    """Return a list of supported tags for each version specified in
    `versions`.

    :param version: a string version, of the form "33" or "32",
        or None. The version will be assumed to support our ABI.
    :param platform: specify the exact platform you want valid
        tags for, or None. If None, use the local system platform.
    :param impl: specify the exact implementation you want valid
        tags for, or None. If None, use the local interpreter impl.
    :param abi: specify the exact abi you want valid
        tags for, or None. If None, use the local interpreter abi.
    """
    supported = []  # type: List[Union[Tag, Tuple[str, str, str]]]

    python_version = None  # type: Optional[PythonVersion]
    if version is not None:
        python_version = _get_python_version(version)

    interpreter = _get_custom_interpreter(impl, version)

    abis = None  # type: Optional[List[str]]
    if abi is not None:
        abis = [abi]

    platforms = None  # type: Optional[List[str]]
    if platform is not None:
        platforms = _get_custom_platforms(platform, platform)

    is_cpython = (impl or interpreter_name()) == "cp"
    if is_cpython:
        supported.extend(
            cpython_tags(
                python_version=python_version,
                abis=abis,
                platforms=platforms,
            )
        )
    else:
        supported.extend(_generic_tags(version, platform, impl, abi))
    supported.extend(
        compatible_tags(
            python_version=python_version,
            interpreter=interpreter,
            platforms=platforms,
        )
    )

    tags = [
        parts if isinstance(parts, Tag) else Tag(*parts)
        for parts in supported
    ]
    return list(_stable_unique_tags(tags))
