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
import sys
from os import stat, walk, getcwd, sep
from copy import copy
from time import time, sleep
from types import ListType
from shutil import copyfile
from os.path import join, splitext, exists, relpath, dirname, basename, split, abspath, isfile, isdir
from inspect import getmro
from copy import deepcopy
from tools.config import Config

from multiprocessing import Pool, cpu_count
from tools.utils import run_cmd, mkdir, rel_path, ToolException, NotSupportedException, split_path
from tools.settings import BUILD_OPTIONS, MBED_ORG_USER
import tools.hooks as hooks
from tools.memap import MemapParser
from hashlib import md5
import fnmatch


#Disables multiprocessing if set to higher number than the host machine CPUs
CPU_COUNT_MIN = 1

def compile_worker(job):
    results = []
    for command in job['commands']:
        try:
            _, _stderr, _rc = run_cmd(command, job['work_dir'])
        except KeyboardInterrupt as e:
            raise ToolException

        results.append({
            'code': _rc,
            'output': _stderr,
            'command': command
        })

    return {
        'source': job['source'],
        'object': job['object'],
        'commands': job['commands'],
        'results': results
    }


class Resources:
    def __init__(self, base_path=None):
        self.base_path = base_path

        self.file_basepath = {}

        self.inc_dirs = []
        self.headers = []

        self.s_sources = []
        self.c_sources = []
        self.cpp_sources = []

        self.lib_dirs = set([])
        self.objects = []
        self.libraries = []

        # mbed special files
        self.lib_builds = []
        self.lib_refs = []

        self.repo_dirs = []
        self.repo_files = []

        self.linker_script = None

        # Other files
        self.hex_files = []
        self.bin_files = []
        self.json_files = []

        # Features
        self.features = {}

    def __add__(self, resources):
        if resources is None:
            return self
        else:
            return self.add(resources)

    def __radd__(self, resources):
        if resources is None:
            return self
        else:
            return self.add(resources)

    def add(self, resources):
        for f,p in resources.file_basepath.items():
            self.file_basepath[f] = p

        self.inc_dirs += resources.inc_dirs
        self.headers += resources.headers

        self.s_sources += resources.s_sources
        self.c_sources += resources.c_sources
        self.cpp_sources += resources.cpp_sources

        self.lib_dirs |= resources.lib_dirs
        self.objects += resources.objects
        self.libraries += resources.libraries

        self.lib_builds += resources.lib_builds
        self.lib_refs += resources.lib_refs

        self.repo_dirs += resources.repo_dirs
        self.repo_files += resources.repo_files

        if resources.linker_script is not None:
            self.linker_script = resources.linker_script

        self.hex_files += resources.hex_files
        self.bin_files += resources.bin_files
        self.json_files += resources.json_files

        self.features.update(resources.features)

        return self

    def relative_to(self, base, dot=False):
        for field in ['inc_dirs', 'headers', 's_sources', 'c_sources',
                      'cpp_sources', 'lib_dirs', 'objects', 'libraries',
                      'lib_builds', 'lib_refs', 'repo_dirs', 'repo_files',
                      'hex_files', 'bin_files', 'json_files']:
            v = [rel_path(f, base, dot) for f in getattr(self, field)]
            setattr(self, field, v)

        self.features = {k: f.relative_to(base, dot) for k, f in self.features.iteritems() if f}

        if self.linker_script is not None:
            self.linker_script = rel_path(self.linker_script, base, dot)

    def win_to_unix(self):
        for field in ['inc_dirs', 'headers', 's_sources', 'c_sources',
                      'cpp_sources', 'lib_dirs', 'objects', 'libraries',
                      'lib_builds', 'lib_refs', 'repo_dirs', 'repo_files',
                      'hex_files', 'bin_files', 'json_files']:
            v = [f.replace('\\', '/') for f in getattr(self, field)]
            setattr(self, field, v)

        self.features = {k: f.win_to_unix() for k, f in self.features.iteritems() if f}

        if self.linker_script is not None:
            self.linker_script = self.linker_script.replace('\\', '/')

    def __str__(self):
        s = []

        for (label, resources) in (
                ('Include Directories', self.inc_dirs),
                ('Headers', self.headers),

                ('Assembly sources', self.s_sources),
                ('C sources', self.c_sources),
                ('C++ sources', self.cpp_sources),

                ('Library directories', self.lib_dirs),
                ('Objects', self.objects),
                ('Libraries', self.libraries),

                ('Hex files', self.hex_files),
                ('Bin files', self.bin_files),

                ('Features', self.features),
            ):
            if resources:
                s.append('%s:\n  ' % label + '\n  '.join(resources))

        if self.linker_script:
            s.append('Linker Script: ' + self.linker_script)

        return '\n'.join(s)

# Support legacy build conventions: the original mbed build system did not have
# standard labels for the "TARGET_" and "TOOLCHAIN_" specific directories, but
# had the knowledge of a list of these directories to be ignored.
LEGACY_IGNORE_DIRS = set([
    'LPC11U24', 'LPC1768', 'LPC2368', 'LPC4088', 'LPC812', 'KL25Z',
    'ARM', 'GCC_ARM', 'GCC_CR', 'IAR', 'uARM'
])
LEGACY_TOOLCHAIN_NAMES = {
    'ARM_STD':'ARM', 'ARM_MICRO': 'uARM',
    'GCC_ARM': 'GCC_ARM', 'GCC_CR': 'GCC_CR',
    'IAR': 'IAR',
}


class mbedToolchain:
    VERBOSE = True

    CORTEX_SYMBOLS = {
        "Cortex-M0" : ["__CORTEX_M0", "ARM_MATH_CM0", "__CMSIS_RTOS", "__MBED_CMSIS_RTOS_CM"],
        "Cortex-M0+": ["__CORTEX_M0PLUS", "ARM_MATH_CM0PLUS", "__CMSIS_RTOS", "__MBED_CMSIS_RTOS_CM"],
        "Cortex-M1" : ["__CORTEX_M3", "ARM_MATH_CM1"],
        "Cortex-M3" : ["__CORTEX_M3", "ARM_MATH_CM3", "__CMSIS_RTOS", "__MBED_CMSIS_RTOS_CM"],
        "Cortex-M4" : ["__CORTEX_M4", "ARM_MATH_CM4", "__CMSIS_RTOS", "__MBED_CMSIS_RTOS_CM"],
        "Cortex-M7" : ["__CORTEX_M7", "ARM_MATH_CM7", "__CMSIS_RTOS", "__MBED_CMSIS_RTOS_CM"],
        "Cortex-A9" : ["__CORTEX_A9", "ARM_MATH_CA9", "__FPU_PRESENT", "__CMSIS_RTOS", "__EVAL", "__MBED_CMSIS_RTOS_CA9"],
    }
    
    CORTEX_FPU_SYMBOLS = {
        "single" : ["__FPU_PRESENT=1"],
        "double" : ["__FPU_PRESENT=1"],
    }

    GOANNA_FORMAT = "[Goanna] warning [%FILENAME%:%LINENO%] - [%CHECKNAME%(%SEVERITY%)] %MESSAGE%"
    GOANNA_DIAGNOSTIC_PATTERN = re.compile(r'"\[Goanna\] (?P<severity>warning) \[(?P<file>[^:]+):(?P<line>\d+)\] \- (?P<message>.*)"')

    MBED_CONFIG_FILE_NAME="mbed_config.h"

    def __init__(self, target, options=None, notify=None, macros=None, silent=False, extra_verbose=False):
        self.target = target
        self.name = self.__class__.__name__

        # compile/assemble/link/binary hooks
        self.hook = hooks.Hook(target, self)

        # Toolchain flags
        self.flags = deepcopy(self.DEFAULT_FLAGS)

        # User-defined macros
        self.macros = macros or []

        # Macros generated from toolchain and target rules/features
        self.symbols = None

        # Labels generated from toolchain and target rules/features (used for selective build)
        self.labels = None

        # This will hold the configuration data (as returned by Config.get_config_data())
        self.config_data = None

        # Non-incremental compile
        self.build_all = False

        # Build output dir
        self.build_dir = None
        self.timestamp = time()

        # Output build naming based on target+toolchain combo (mbed 2.0 builds)
        self.obj_path = join("TARGET_"+target.name, "TOOLCHAIN_"+self.name)

        # Number of concurrent build jobs. 0 means auto (based on host system cores)
        self.jobs = 0

        self.CHROOT = None

        # Ignore patterns from .mbedignore files
        self.ignore_patterns = []

        # Pre-mbed 2.0 ignore dirs
        self.legacy_ignore_dirs = LEGACY_IGNORE_DIRS - set([target.name, LEGACY_TOOLCHAIN_NAMES[self.name]])

        # Output notify function
        # This function is passed all events, and expected to handle notification of the
        # user, emit the events to a log, etc.
        # The API for all notify methods passed into the notify parameter is as follows:
        # def notify(Event, Silent)
        # Where *Event* is a dict representing the toolchain event that was generated
        #            e.g.: a compile succeeded, or a warning was emitted by the compiler
        #                  or an application was linked
        #       *Silent* is a boolean
        if notify:
            self.notify_fun = notify
        elif extra_verbose:
            self.notify_fun = self.print_notify_verbose
        else:
            self.notify_fun = self.print_notify

        # Silent builds (no output)
        self.silent = silent

        # Print output buffer
        self.output = str()
        self.map_outputs = list()   # Place to store memmap scan results in JSON like data structures

        # Build options passed by -o flag
        self.options = options if options is not None else []

        # Build options passed by settings.py or mbed_settings.py
        self.options.extend(BUILD_OPTIONS)

        if self.options:
            self.info("Build Options: %s" % (', '.join(self.options)))

        # uVisor spepcific rules
        if 'UVISOR' in self.target.features and 'UVISOR_SUPPORTED' in self.target.extra_labels:
            self.target.core = re.sub(r"F$", '', self.target.core)

    def get_output(self):
        return self.output

    def print_notify(self, event, silent=False):
        """ Default command line notification
        """
        msg = None

        if not self.VERBOSE and event['type'] == 'tool_error':
            msg = event['message']

        elif event['type'] in ['info', 'debug']:
            msg = event['message']

        elif event['type'] == 'cc':
            event['severity'] = event['severity'].title()
            event['file'] = basename(event['file'])
            msg = '[%(severity)s] %(file)s@%(line)s: %(message)s' % event

        elif event['type'] == 'progress':
            if not silent:
                msg = '%s: %s' % (event['action'].title(), basename(event['file']))

        if msg:
            print msg
            self.output += msg + "\n"

    def print_notify_verbose(self, event, silent=False):
        """ Default command line notification with more verbose mode
        """
        if event['type'] in ['info', 'debug']:
            self.print_notify(event) # standard handle

        elif event['type'] == 'cc':
            event['severity'] = event['severity'].title()
            event['file'] = basename(event['file'])
            event['mcu_name'] = "None"
            event['target_name'] = event['target_name'].upper() if event['target_name'] else "Unknown"
            event['toolchain_name'] = event['toolchain_name'].upper() if event['toolchain_name'] else "Unknown"
            msg = '[%(severity)s] %(target_name)s::%(toolchain_name)s::%(file)s@%(line)s: %(message)s' % event
            print msg
            self.output += msg + "\n"

        elif event['type'] == 'progress':
            self.print_notify(event) # standard handle

    def notify(self, event):
        """ Little closure for notify functions
        """
        event['toolchain'] = self
        return self.notify_fun(event, self.silent)

    def goanna_parse_line(self, line):
        if "analyze" in self.options:
            return self.GOANNA_DIAGNOSTIC_PATTERN.match(line)
        else:
            return None

    def get_symbols(self):
        if self.symbols is None:
            # Target and Toolchain symbols
            labels = self.get_labels()
            self.symbols = ["TARGET_%s" % t for t in labels['TARGET']]
            self.symbols.extend(["TOOLCHAIN_%s" % t for t in labels['TOOLCHAIN']])

            # Cortex CPU symbols
            if self.target.core in mbedToolchain.CORTEX_SYMBOLS:
                self.symbols.extend(mbedToolchain.CORTEX_SYMBOLS[self.target.core])
            if self.target.fpu in mbedToolchain.CORTEX_FPU_SYMBOLS:
                self.symbols.extend(mbedToolchain.CORTEX_FPU_SYMBOLS[self.target.fpu])

            # Symbols defined by the on-line build.system
            self.symbols.extend(['MBED_BUILD_TIMESTAMP=%s' % self.timestamp, 'TARGET_LIKE_MBED', '__MBED__=1'])
            if MBED_ORG_USER:
                self.symbols.append('MBED_USERNAME=' + MBED_ORG_USER)

            # Add target's symbols
            self.symbols += self.target.macros
            # Add target's hardware
            self.symbols += ["DEVICE_" + data + "=1" for data in self.target.device_has]
            # Add target's features
            self.symbols += ["FEATURE_" + data + "=1" for data in self.target.features]
            # Add extra symbols passed via 'macros' parameter
            self.symbols += self.macros

            # Form factor variables
            if hasattr(self.target, 'supported_form_factors'):
                self.symbols.extend(["TARGET_FF_%s" % t for t in self.target.supported_form_factors])

        return list(set(self.symbols))  # Return only unique symbols

    # Extend the internal list of macros
    def add_macros(self, new_macros):
        self.macros.extend(new_macros)

    def get_labels(self):
        if self.labels is None:
            toolchain_labels = [c.__name__ for c in getmro(self.__class__)]
            toolchain_labels.remove('mbedToolchain')
            self.labels = {
                'TARGET': self.target.get_labels() + ["DEBUG" if "debug-info" in self.options else "RELEASE"],
                'FEATURE': self.target.features,
                'TOOLCHAIN': toolchain_labels
            }
        return self.labels

    def need_update(self, target, dependencies):
        if self.build_all:
            return True

        if not exists(target):
            return True

        target_mod_time = stat(target).st_mtime

        for d in dependencies:

            # Some objects are not provided with full path and here we do not have
            # information about the library paths. Safe option: assume an update
            if not d or not exists(d):
                return True

            if stat(d).st_mtime >= target_mod_time:
                return True

        return False

    def is_ignored(self, file_path):
        for pattern in self.ignore_patterns:
            if fnmatch.fnmatch(file_path, pattern):
                return True
        return False

    # Create a Resources object from the path pointed to by *path* by either traversing a
    # a directory structure, when *path* is a directory, or adding *path* to the resources,
    # when *path* is a file.
    # The parameter *base_path* is used to set the base_path attribute of the Resources
    # object and the parameter *exclude_paths* is used by the directory traversal to
    # exclude certain paths from the traversal.
    def scan_resources(self, path, exclude_paths=None, base_path=None):
        resources = Resources(path)
        if not base_path:
            if isfile(path):
                base_path = dirname(path)
            else:
                base_path = path
        resources.base_path = base_path

        if isfile(path):
            self._add_file(path, resources, base_path, exclude_paths=exclude_paths)
        else:
            self._add_dir(path, resources, base_path, exclude_paths=exclude_paths)
        return resources

    # A helper function for scan_resources. _add_dir traverses *path* (assumed to be a
    # directory) and heeds the ".mbedignore" files along the way. _add_dir calls _add_file
    # on every file it considers adding to the resources object.
    def _add_dir(self, path, resources, base_path, exclude_paths=None):
        """ os.walk(top[, topdown=True[, onerror=None[, followlinks=False]]])
        When topdown is True, the caller can modify the dirnames list in-place
        (perhaps using del or slice assignment), and walk() will only recurse into
        the subdirectories whose names remain in dirnames; this can be used to prune
        the search, impose a specific order of visiting, or even to inform walk()
        about directories the caller creates or renames before it resumes walk()
        again. Modifying dirnames when topdown is False is ineffective, because in
        bottom-up mode the directories in dirnames are generated before dirpath
        itself is generated.
        """
        labels = self.get_labels()
        for root, dirs, files in walk(path, followlinks=True):
            # Check if folder contains .mbedignore
            if ".mbedignore" in files:
                with open (join(root,".mbedignore"), "r") as f:
                    lines=f.readlines()
                    lines = [l.strip() for l in lines] # Strip whitespaces
                    lines = [l for l in lines if l != ""] # Strip empty lines
                    lines = [l for l in lines if not re.match("^#",l)] # Strip comment lines
                    # Append root path to glob patterns and append patterns to ignore_patterns
                    self.ignore_patterns.extend([join(root,line.strip()) for line in lines])

            # Skip the whole folder if ignored, e.g. .mbedignore containing '*'
            if self.is_ignored(join(root,"")):
                continue

            for d in copy(dirs):
                dir_path = join(root, d)
                # Add internal repo folders/files. This is needed for exporters
                if d == '.hg':
                    resources.repo_dirs.append(dir_path)
                    resources.repo_files.extend(self.scan_repository(dir_path))

                if ((d.startswith('.') or d in self.legacy_ignore_dirs) or
                    # Ignore targets that do not match the TARGET in extra_labels list
                    (d.startswith('TARGET_') and d[7:] not in labels['TARGET']) or
                    # Ignore toolchain that do not match the current TOOLCHAIN
                    (d.startswith('TOOLCHAIN_') and d[10:] not in labels['TOOLCHAIN']) or
                    # Ignore .mbedignore files
                    self.is_ignored(join(dir_path,"")) or
                    # Ignore TESTS dir
                    (d == 'TESTS')):
                        dirs.remove(d)
                elif d.startswith('FEATURE_'):
                    # Recursively scan features but ignore them in the current scan.
                    # These are dynamically added by the config system if the conditions are matched
                    resources.features[d[8:]] = self.scan_resources(dir_path, base_path=base_path)
                    dirs.remove(d)
                elif exclude_paths:
                    for exclude_path in exclude_paths:
                        rel_path = relpath(dir_path, exclude_path)
                        if not (rel_path.startswith('..')):
                            dirs.remove(d)
                            break

            # Add root to include paths
            resources.inc_dirs.append(root)

            for file in files:
                file_path = join(root, file)
                self._add_file(file_path, resources, base_path)

    # A helper function for both scan_resources and _add_dir. _add_file adds one file
    # (*file_path*) to the resources object based on the file type.
    def _add_file(self, file_path, resources, base_path, exclude_paths=None):
        resources.file_basepath[file_path] = base_path

        if self.is_ignored(file_path):
            return

        _, ext = splitext(file_path)
        ext = ext.lower()

        if   ext == '.s':
            resources.s_sources.append(file_path)

        elif ext == '.c':
            resources.c_sources.append(file_path)

        elif ext == '.cpp':
            resources.cpp_sources.append(file_path)

        elif ext == '.h' or ext == '.hpp':
            resources.headers.append(file_path)

        elif ext == '.o':
            resources.objects.append(file_path)

        elif ext == self.LIBRARY_EXT:
            resources.libraries.append(file_path)
            resources.lib_dirs.add(dirname(file_path))

        elif ext == self.LINKER_EXT:
            if resources.linker_script is not None:
                self.info("Warning: Multiple linker scripts detected: %s -> %s" % (resources.linker_script, file_path))
            resources.linker_script = file_path

        elif ext == '.lib':
            resources.lib_refs.append(file_path)

        elif ext == '.bld':
            resources.lib_builds.append(file_path)

        elif file == '.hgignore':
            resources.repo_files.append(file_path)

        elif ext == '.hex':
            resources.hex_files.append(file_path)

        elif ext == '.bin':
            resources.bin_files.append(file_path)

        elif ext == '.json':
            resources.json_files.append(file_path)


    def scan_repository(self, path):
        resources = []

        for root, dirs, files in walk(path):
            # Remove ignored directories
            for d in copy(dirs):
                if d == '.' or d == '..':
                    dirs.remove(d)

            for file in files:
                file_path = join(root, file)
                resources.append(file_path)

        return resources

    def copy_files(self, files_paths, trg_path, resources=None, rel_path=None):

        # Handle a single file
        if type(files_paths) != ListType: files_paths = [files_paths]

        for source in files_paths:
            if source is None:
                files_paths.remove(source)

        for source in files_paths:
            if resources is not None:
                relative_path = relpath(source, resources.file_basepath[source])
            elif rel_path is not None:
                relative_path = relpath(source, rel_path)
            else:
                _, relative_path = split(source)

            target = join(trg_path, relative_path)

            if (target != source) and (self.need_update(target, [source])):
                self.progress("copy", relative_path)
                mkdir(dirname(target))
                copyfile(source, target)

    def relative_object_path(self, build_path, base_dir, source):
        source_dir, name, _ = split_path(source)

        obj_dir = join(build_path, relpath(source_dir, base_dir))
        mkdir(obj_dir)
        return join(obj_dir, name + '.o')

    def get_inc_file(self, includes):
        include_file = join(self.build_dir, ".includes_%s.txt" % self.inc_md5)
        if not exists(include_file):
            with open(include_file, "wb") as f:
                cmd_list = []
                for c in includes:
                    if c:
                        cmd_list.append(('-I%s' % c).replace("\\", "/"))
                string = " ".join(cmd_list)
                f.write(string)
        return include_file

    def compile_sources(self, resources, build_path, inc_dirs=None):
        # Web IDE progress bar for project build
        files_to_compile = resources.s_sources + resources.c_sources + resources.cpp_sources
        self.to_be_compiled = len(files_to_compile)
        self.compiled = 0

        inc_paths = resources.inc_dirs
        if inc_dirs is not None:
            inc_paths.extend(inc_dirs)
        # De-duplicate include paths
        inc_paths = set(inc_paths)
        # Sort include paths for consistency
        inc_paths = sorted(set(inc_paths))
        # Unique id of all include paths
        self.inc_md5 = md5(' '.join(inc_paths)).hexdigest()
        # Where to store response files
        self.build_dir = build_path

        objects = []
        queue = []
        prev_dir = None

        # Sort compile queue for consistency
        files_to_compile.sort()
        work_dir = getcwd()

        for source in files_to_compile:
            _, name, _ = split_path(source)
            object = self.relative_object_path(build_path, resources.file_basepath[source], source)

            # Queue mode (multiprocessing)
            commands = self.compile_command(source, object, inc_paths)
            if commands is not None:
                queue.append({
                    'source': source,
                    'object': object,
                    'commands': commands,
                    'work_dir': work_dir,
                    'chroot': self.CHROOT
                })
            else:
                objects.append(object)

        # Use queues/multiprocessing if cpu count is higher than setting
        jobs = self.jobs if self.jobs else cpu_count()
        if jobs > CPU_COUNT_MIN and len(queue) > jobs:
            return self.compile_queue(queue, objects)
        else:
            return self.compile_seq(queue, objects)

    def compile_seq(self, queue, objects):
        for item in queue:
            result = compile_worker(item)

            self.compiled += 1
            self.progress("compile", item['source'], build_update=True)
            for res in result['results']:
                self.debug("Command: %s" % ' '.join(res['command']))
                self.compile_output([
                    res['code'],
                    res['output'],
                    res['command']
                ])
            objects.append(result['object'])
        return objects

    def compile_queue(self, queue, objects):
        jobs_count = int(self.jobs if self.jobs else cpu_count())
        p = Pool(processes=jobs_count)

        results = []
        for i in range(len(queue)):
            results.append(p.apply_async(compile_worker, [queue[i]]))

        itr = 0
        while True:
            itr += 1
            if itr > 180000:
                p.terminate()
                p.join()
                raise ToolException("Compile did not finish in 5 minutes")

            pending = 0
            for r in results:
                if r._ready is True:
                    try:
                        result = r.get()
                        results.remove(r)

                        self.compiled += 1
                        self.progress("compile", result['source'], build_update=True)
                        for res in result['results']:
                            self.debug("Command: %s" % ' '.join(res['command']))
                            self.compile_output([
                                res['code'],
                                res['output'],
                                res['command']
                            ])
                        objects.append(result['object'])
                    except ToolException, err:
                        p.terminate()
                        p.join()
                        raise ToolException(err)
                else:
                    pending += 1
                    if pending > jobs_count:
                        break


            if len(results) == 0:
                break

            sleep(0.01)

        results = None
        p.terminate()
        p.join()

        return objects

    def compile_command(self, source, object, includes):
        # Check dependencies
        _, ext = splitext(source)
        ext = ext.lower()

        if ext == '.c' or  ext == '.cpp':
            base, _ = splitext(object)
            dep_path = base + '.d'
            deps = self.parse_dependencies(dep_path) if (exists(dep_path)) else []
            if len(deps) == 0 or self.need_update(object, deps):
                if ext == '.c':
                    return self.compile_c(source, object, includes)
                else:
                    return self.compile_cpp(source, object, includes)
        elif ext == '.s':
            deps = [source]
            if self.need_update(object, deps):
                return self.assemble(source, object, includes)
        else:
            return False

        return None

    def is_not_supported_error(self, output):
        return "#error directive: [NOT_SUPPORTED]" in output

    def compile_output(self, output=[]):
        _rc = output[0]
        _stderr = output[1]
        command = output[2]

        # Parse output for Warnings and Errors
        self.parse_output(_stderr)
        self.debug("Return: %s"% _rc)
        for error_line in _stderr.splitlines():
            self.debug("Output: %s"% error_line)


        # Check return code
        if _rc != 0:
            for line in _stderr.splitlines():
                self.tool_error(line)

            if self.is_not_supported_error(_stderr):
                raise NotSupportedException(_stderr)
            else:
                raise ToolException(_stderr)

    def build_library(self, objects, dir, name):
        needed_update = False
        lib = self.STD_LIB_NAME % name
        fout = join(dir, lib)
        if self.need_update(fout, objects):
            self.info("Library: %s" % lib)
            self.archive(objects, fout)
            needed_update = True

        return needed_update

    def link_program(self, r, tmp_path, name):
        needed_update = False
        ext = 'bin'
        if hasattr(self.target, 'OUTPUT_EXT'):
            ext = self.target.OUTPUT_EXT

        if hasattr(self.target, 'OUTPUT_NAMING'):
            self.var("binary_naming", self.target.OUTPUT_NAMING)
            if self.target.OUTPUT_NAMING == "8.3":
                name = name[0:8]
                ext = ext[0:3]

        # Create destination directory
        head, tail =  split(name)
        new_path = join(tmp_path, head)
        mkdir(new_path)

        filename = name+'.'+ext
        elf = join(tmp_path, name + '.elf')
        bin = join(tmp_path, filename)
        map = join(tmp_path, name + '.map')

        if self.need_update(elf, r.objects + r.libraries + [r.linker_script]):
            needed_update = True
            self.progress("link", name)
            self.link(elf, r.objects, r.libraries, r.lib_dirs, r.linker_script)

        if self.need_update(bin, [elf]):
            needed_update = True
            self.progress("elf2bin", name)

            self.binary(r, elf, bin)

        self.map_outputs = self.mem_stats(map)

        self.var("compile_succeded", True)
        self.var("binary", filename)

        return bin, needed_update

    def default_cmd(self, command):
        self.debug("Command: %s"% ' '.join(command))
        _stdout, _stderr, _rc = run_cmd(command)

        self.debug("Return: %s"% _rc)

        for output_line in _stdout.splitlines():
            self.debug("Output: %s"% output_line)
        for error_line in _stderr.splitlines():
            self.debug("Errors: %s"% error_line)

        if _rc != 0:
            for line in _stderr.splitlines():
                self.tool_error(line)
            raise ToolException(_stderr)

    ### NOTIFICATIONS ###
    def info(self, message):
        self.notify({'type': 'info', 'message': message})

    def debug(self, message):
        if self.VERBOSE:
            if type(message) is ListType:
                message = ' '.join(message)
            message = "[DEBUG] " + message
            self.notify({'type': 'debug', 'message': message})

    def cc_info(self, severity, file, line, message, target_name=None, toolchain_name=None):
        self.notify({'type': 'cc',
                     'severity': severity,
                     'file': file,
                     'line': line,
                     'message': message,
                     'target_name': target_name,
                     'toolchain_name': toolchain_name})

    def progress(self, action, file, build_update=False):
        msg = {'type': 'progress', 'action': action, 'file': file}
        if build_update:
            msg['percent'] = 100. * float(self.compiled) / float(self.to_be_compiled)
        self.notify(msg)

    def tool_error(self, message):
        self.notify({'type': 'tool_error', 'message': message})

    def var(self, key, value):
        self.notify({'type': 'var', 'key': key, 'val': value})

    def mem_stats(self, map):
        """! Creates parser object
        @param map Path to linker map file to parse and decode
        @return Memory summary structure with memory usage statistics
                None if map file can't be opened and processed
        """
        toolchain = self.__class__.__name__

        # Create memap object
        memap = MemapParser()

        # Parse and decode a map file
        if memap.parse(abspath(map), toolchain) is False:
            self.info("Unknown toolchain for memory statistics %s" % toolchain)
            return None

        # Write output to stdout in text (pretty table) format
        memap.generate_output('table')

        # Write output to file in JSON format
        map_out = splitext(map)[0] + "_map.json"
        memap.generate_output('json', map_out)

        # Write output to file in CSV format for the CI
        map_csv = splitext(map)[0] + "_map.csv"
        memap.generate_output('csv-ci', map_csv)

        # Here we return memory statistics structure (constructed after
        # call to generate_output) which contains raw data in bytes
        # about sections + summary
        return memap.get_memory_summary()

    # Set the configuration data
    def set_config_data(self, config_data):
        self.config_data = config_data

    # Return the location of the config header. This function will create the config
    # header first if needed. The header will be written in a file called "mbed_conf.h"
    # located in the project's build directory.
    # If config headers are not used (self.config_header_content is None), the function
    # returns None
    def get_config_header(self):
        if self.config_data is None:
            return None
        config_file = join(self.build_dir, self.MBED_CONFIG_FILE_NAME)
        if not exists(config_file):
            with open(config_file, "wt") as f:
                f.write(Config.config_to_header(self.config_data))
        return config_file

    # Return the list of macros geenrated by the build system
    def get_config_macros(self):
        return Config.config_to_macros(self.config_data) if self.config_data else []

from tools.settings import ARM_BIN
from tools.settings import GCC_ARM_PATH, GCC_CR_PATH
from tools.settings import IAR_PATH

TOOLCHAIN_BIN_PATH = {
    'ARM': ARM_BIN,
    'uARM': ARM_BIN,
    'GCC_ARM': GCC_ARM_PATH,
    'GCC_CR': GCC_CR_PATH,
    'IAR': IAR_PATH
}

from tools.toolchains.arm import ARM_STD, ARM_MICRO
from tools.toolchains.gcc import GCC_ARM, GCC_CR
from tools.toolchains.iar import IAR

TOOLCHAIN_CLASSES = {
    'ARM': ARM_STD,
    'uARM': ARM_MICRO,
    'GCC_ARM': GCC_ARM,
    'GCC_CR': GCC_CR,
    'IAR': IAR
}

TOOLCHAINS = set(TOOLCHAIN_CLASSES.keys())
