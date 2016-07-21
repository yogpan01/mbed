"""
mbed SDK
Copyright (c) 2011-2013 ARM Limited

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import re
from os.path import join, basename, splitext, dirname, exists

from tools.toolchains import mbedToolchain
from tools.settings import GCC_ARM_PATH, GCC_CR_PATH
from tools.settings import GOANNA_PATH
from tools.hooks import hook_tool

class GCC(mbedToolchain):
    LINKER_EXT = '.ld'
    LIBRARY_EXT = '.a'

    STD_LIB_NAME = "lib%s.a"
    DIAGNOSTIC_PATTERN = re.compile('((?P<file>[^:]+):(?P<line>\d+):)(\d+:)? (?P<severity>warning|error): (?P<message>.+)')

    DEFAULT_FLAGS = {
        'common': ["-c", "-Wall", "-Wextra",
            "-Wno-unused-parameter", "-Wno-missing-field-initializers",
            "-fmessage-length=0", "-fno-exceptions", "-fno-builtin",
            "-ffunction-sections", "-fdata-sections", "-funsigned-char",
            "-MMD", "-fno-delete-null-pointer-checks", "-fomit-frame-pointer"
            ],
        'asm': ["-x", "assembler-with-cpp"],
        'c': ["-std=gnu99"],
        'cxx': ["-std=gnu++98", "-fno-rtti", "-Wvla"],
        'ld': ["-Wl,--gc-sections", "-Wl,--wrap,main",
            "-Wl,--wrap,_malloc_r", "-Wl,--wrap,_free_r", "-Wl,--wrap,_realloc_r"],
    }

    def __init__(self, target, options=None, notify=None, macros=None, silent=False, tool_path="", extra_verbose=False):
        mbedToolchain.__init__(self, target, options, notify, macros, silent, extra_verbose=extra_verbose)

        if target.core == "Cortex-M0+":
            cpu = "cortex-m0plus"
        else:
            cpu = target.core.lower()

        self.cpu = ["-mcpu=%s" % cpu]
        if target.core.startswith("Cortex"):
            self.cpu.append("-mthumb")

        if target.core == "Cortex-M4" and target.fpu == "single":
            self.cpu.append("-mfpu=fpv4-sp-d16")
            self.cpu.append("-mfloat-abi=softfp")

        elif target.core == "Cortex-M7" and target.fpu == "single":
            self.cpu.append("-mfpu=fpv5-sp-d16")
            self.cpu.append("-mfloat-abi=softfp")

        elif target.core == "Cortex-M7" and target.fpu == "double":
            self.cpu.append("-mfpu=fpv5-d16")
            self.cpu.append("-mfloat-abi=softfp")

        if target.core == "Cortex-A9":
            self.cpu.append("-mthumb-interwork")
            self.cpu.append("-marm")
            self.cpu.append("-march=armv7-a")
            self.cpu.append("-mfpu=vfpv3")
            self.cpu.append("-mfloat-abi=hard")
            self.cpu.append("-mno-unaligned-access")


        # Note: We are using "-O2" instead of "-Os" to avoid this known GCC bug:
        # http://gcc.gnu.org/bugzilla/show_bug.cgi?id=46762
        self.flags["common"] += self.cpu

        if "save-asm" in self.options:
            self.flags["common"].append("-save-temps")

        if "debug-info" in self.options:
            self.flags["common"].append("-g")
            self.flags["common"].append("-O0")
        else:
            self.flags["common"].append("-Os")

        main_cc = join(tool_path, "arm-none-eabi-gcc")
        main_cppc = join(tool_path, "arm-none-eabi-g++")
        self.asm = [main_cc] + self.flags['asm'] + self.flags["common"]
        if not "analyze" in self.options:
            self.cc  = [main_cc]
            self.cppc =[main_cppc]
        else:
            self.cc  = [join(GOANNA_PATH, "goannacc"), "--with-cc=" + main_cc.replace('\\', '/'), "--dialect=gnu", '--output-format="%s"' % self.GOANNA_FORMAT]
            self.cppc= [join(GOANNA_PATH, "goannac++"), "--with-cxx=" + main_cppc.replace('\\', '/'),  "--dialect=gnu", '--output-format="%s"' % self.GOANNA_FORMAT]
        self.cc += self.flags['c'] + self.flags['common']
        self.cppc += self.flags['cxx'] + self.flags['common']

        self.flags['ld'] += self.cpu
        self.ld = [join(tool_path, "arm-none-eabi-gcc")] + self.flags['ld']
        self.sys_libs = ["stdc++", "supc++", "m", "c", "gcc"]

        self.ar = join(tool_path, "arm-none-eabi-ar")
        self.elf2bin = join(tool_path, "arm-none-eabi-objcopy")

    def parse_dependencies(self, dep_path):
        dependencies = []
        buff = open(dep_path).readlines()
        buff[0] = re.sub('^(.*?)\: ', '', buff[0])
        for line in buff:
            file = line.replace('\\\n', '').strip()
            if file:
                # GCC might list more than one dependency on a single line, in this case
                # the dependencies are separated by a space. However, a space might also
                # indicate an actual space character in a dependency path, but in this case
                # the space character is prefixed by a backslash.
                # Temporary replace all '\ ' with a special char that is not used (\a in this
                # case) to keep them from being interpreted by 'split' (they will be converted
                # back later to a space char)
                file = file.replace('\\ ', '\a')
                if file.find(" ") == -1:
                    dependencies.append(file.replace('\a', ' '))
                else:
                    dependencies = dependencies + [f.replace('\a', ' ') for f in file.split(" ")]
        return dependencies

    def is_not_supported_error(self, output):
        return "error: #error [NOT_SUPPORTED]" in output

    def parse_output(self, output):
        # The warning/error notification is multiline
        WHERE, WHAT = 0, 1
        state, file, message = WHERE, None, None
        for line in output.splitlines():
            match = self.goanna_parse_line(line)
            if match is not None:
                self.cc_info(
                    match.group('severity').lower(),
                    match.group('file'),
                    match.group('line'),
                    match.group('message'),
                    target_name=self.target.name,
                    toolchain_name=self.name
                )
                continue


            match = GCC.DIAGNOSTIC_PATTERN.match(line)
            if match is not None:
                self.cc_info(
                    match.group('severity').lower(),
                    match.group('file'),
                    match.group('line'),
                    match.group('message'),
                    target_name=self.target.name,
                    toolchain_name=self.name
                )

    def get_dep_option(self, object):
        base, _ = splitext(object)
        dep_path = base + '.d'
        return ["-MD", "-MF", dep_path]

    def get_config_option(self, config_header):
        return ['-include', config_header]

    def get_compile_options(self, defines, includes):
        opts = ['-D%s' % d for d in defines] + ['@%s' % self.get_inc_file(includes)]
        config_header = self.get_config_header()
        if config_header is not None:
            opts = opts + self.get_config_option(config_header)
        return opts

    @hook_tool
    def assemble(self, source, object, includes):
        # Build assemble command
        cmd = self.asm + self.get_compile_options(self.get_symbols(), includes) + ["-o", object, source]

        # Call cmdline hook
        cmd = self.hook.get_cmdline_assembler(cmd)

        # Return command array, don't execute
        return [cmd]

    @hook_tool
    def compile(self, cc, source, object, includes):
        # Build compile command
        cmd = cc + self.get_compile_options(self.get_symbols(), includes)

        cmd.extend(self.get_dep_option(object))

        cmd.extend(["-o", object, source])

        # Call cmdline hook
        cmd = self.hook.get_cmdline_compiler(cmd)

        return [cmd]

    def compile_c(self, source, object, includes):
        return self.compile(self.cc, source, object, includes)

    def compile_cpp(self, source, object, includes):
        return self.compile(self.cppc, source, object, includes)

    @hook_tool
    def link(self, output, objects, libraries, lib_dirs, mem_map):
        libs = []
        for l in libraries:
            name, _ = splitext(basename(l))
            libs.append("-l%s" % name[3:])
        libs.extend(["-l%s" % l for l in self.sys_libs])

        # Build linker command
        map_file = splitext(output)[0] + ".map"
        cmd = self.ld + ["-o", output, "-Wl,-Map=%s" % map_file] + objects + ["-Wl,--start-group"] + libs + ["-Wl,--end-group"]
        if mem_map:
            cmd.extend(['-T', mem_map])

        for L in lib_dirs:
            cmd.extend(['-L', L])
        cmd.extend(libs)

        # Call cmdline hook
        cmd = self.hook.get_cmdline_linker(cmd)

        # Split link command to linker executable + response file
        link_files = join(dirname(output), ".link_files.txt")
        with open(link_files, "wb") as f:
            cmd_linker = cmd[0]
            cmd_list = []
            for c in cmd[1:]:
                if c:
                    cmd_list.append(('"%s"' % c) if not c.startswith('-') else c)
            string = " ".join(cmd_list).replace("\\", "/")
            f.write(string)

        # Exec command
        self.default_cmd([cmd_linker, "@%s" % link_files])

    @hook_tool
    def archive(self, objects, lib_path):
        archive_files = join(dirname(lib_path), ".archive_files.txt")
        with open(archive_files, "wb") as f:
            o_list = []
            for o in objects:
                o_list.append('"%s"' % o)
            string = " ".join(o_list).replace("\\", "/")
            f.write(string)

        # Exec command
        self.default_cmd([self.ar, 'rcs', lib_path, "@%s" % archive_files])

    @hook_tool
    def binary(self, resources, elf, bin):
        # Build binary command
        cmd = [self.elf2bin, "-O", "binary", elf, bin]

        # Call cmdline hook
        cmd = self.hook.get_cmdline_binary(cmd)

        # Exec command
        self.default_cmd(cmd)


class GCC_ARM(GCC):
    def __init__(self, target, options=None, notify=None, macros=None, silent=False, extra_verbose=False):
        GCC.__init__(self, target, options, notify, macros, silent, GCC_ARM_PATH, extra_verbose=extra_verbose)

        # Use latest gcc nanolib
        if "big-build" in self.options:
            use_nano = False
        elif "small-build" in self.options:
            use_nano = True
        elif target.default_build == "standard":
            use_nano = False
        elif target.default_build == "small":
            use_nano = True
        else:
            use_nano = False

        if use_nano:
            self.ld.append("--specs=nano.specs")
            self.flags['ld'].append("--specs=nano.specs")
            self.cc += ["-DMBED_RTOS_SINGLE_THREAD"]
            self.cppc += ["-DMBED_RTOS_SINGLE_THREAD"]
            self.macros.extend(["MBED_RTOS_SINGLE_THREAD"])

        if target.name in ["LPC1768", "LPC4088", "LPC4088_DM", "LPC4330", "UBLOX_C027", "LPC2368", "ARM_BEETLE_SOC"]:
            self.ld.extend(["-u _printf_float", "-u _scanf_float"])
            self.flags['ld'].extend(["-u _printf_float", "-u _scanf_float"])
        elif target.name in ["RZ_A1H", "VK_RZ_A1H", "ARCH_MAX", "DISCO_F407VG", "DISCO_F429ZI", "DISCO_F469NI", "NUCLEO_F401RE", "NUCLEO_F410RB", "NUCLEO_F411RE", "NUCLEO_F429ZI", "NUCLEO_F446RE", "NUCLEO_F446ZE", "ELMO_F411RE", "MTS_MDOT_F411RE", "MTS_DRAGONFLY_F411RE", "DISCO_F746NG"]:
            self.ld.extend(["-u_printf_float", "-u_scanf_float"])
            self.flags['ld'].extend(["-u_printf_float", "-u_scanf_float"])

        self.sys_libs.append("nosys")


class GCC_CR(GCC):
    def __init__(self, target, options=None, notify=None, macros=None, silent=False, extra_verbose=False):
        GCC.__init__(self, target, options, notify, macros, silent, GCC_CR_PATH, extra_verbose=extra_verbose)

        additional_compiler_flags = [
            "-D__NEWLIB__", "-D__CODE_RED", "-D__USE_CMSIS", "-DCPP_USE_HEAP",
        ]
        self.cc += additional_compiler_flags
        self.cppc += additional_compiler_flags

        # Use latest gcc nanolib
        self.ld.append("--specs=nano.specs")
        if target.name in ["LPC1768", "LPC4088", "LPC4088_DM", "LPC4330", "UBLOX_C027", "LPC2368"]:
            self.ld.extend(["-u _printf_float", "-u _scanf_float"])
        self.ld += ["-nostdlib"]
