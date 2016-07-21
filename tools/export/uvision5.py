"""
mbed SDK
Copyright (c) 2016 ARM Limited

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
from os.path import basename, join, dirname
from project_generator_definitions.definitions import ProGenDef

from tools.export.exporters import Exporter
from tools.targets import TARGET_MAP, TARGET_NAMES
from tools.settings import ARM_INC

# If you wish to add a new target, add it to project_generator_definitions, and then
# define progen_target name in the target class (`` self.progen_target = 'my_target_name' ``)
# There are 2 default mbed templates (predefined settings) uvision.uvproj and uvproj_microlib.uvproj.tmpl
class Uvision5(Exporter):
    """
    Exporter class for uvision5. This class uses project generator.
    """
    # These 2 are currently for exporters backward compatiblity
    NAME = 'uVision5'
    TOOLCHAIN = 'ARM'
    # PROGEN_ACTIVE contains information for exporter scripts that this is using progen
    PROGEN_ACTIVE = True

    MBED_CONFIG_HEADER_SUPPORTED = True

    # backward compatibility with our scripts
    def __init__(self):
        self._targets = []

    @property
    def TARGETS(self):
        if not hasattr(self, "_targets_supported"):
            self._targets_supported = []
            for target in TARGET_NAMES:
                try:
                    if (ProGenDef('uvision5').is_supported(str(TARGET_MAP[target])) or
                        ProGenDef('uvision5').is_supported(TARGET_MAP[target].progen['target'])):
                        self._targets_supported.append(target)
                except AttributeError:
                    # target is not supported yet
                    continue
        return self._targets_supported

    def get_toolchain(self):
        return TARGET_MAP[self.target].default_toolchain

    def generate(self, progen_build=False):
        """ Generates the project files """
        project_data = self.progen_get_project_data()
        tool_specific = {}
        # Expand tool specific settings by uvision specific settings which are required
        try:
            if TARGET_MAP[self.target].progen['uvision5']['template']:
                tool_specific['uvision5'] = TARGET_MAP[self.target].progen['uvision5']
        except KeyError:
            # use default template
            # by the mbed projects
            tool_specific['uvision5'] = {
                    'template': [join(dirname(__file__),  'uvision.uvproj.tmpl')],
            }

        project_data['tool_specific'] = {}
        project_data['tool_specific'].update(tool_specific)

        # get flags from toolchain and apply
        project_data['tool_specific']['uvision5']['misc'] = {}
        # asm flags only, common are not valid within uvision project, they are armcc specific
        project_data['tool_specific']['uvision5']['misc']['asm_flags'] = list(set(self.progen_flags['asm_flags']))
        # cxx flags included, as uvision have them all in one tab
        project_data['tool_specific']['uvision5']['misc']['c_flags'] = list(set(self.progen_flags['common_flags'] + self.progen_flags['c_flags'] + self.progen_flags['cxx_flags']))
        # ARM_INC is by default as system inclusion, not required for exported project
        project_data['tool_specific']['uvision5']['misc']['c_flags'].remove("-I \""+ARM_INC+"\"")
        # not compatible with c99 flag set in the template
        project_data['tool_specific']['uvision5']['misc']['c_flags'].remove("--c99")
        # cpp is not required as it's implicit for cpp files
        project_data['tool_specific']['uvision5']['misc']['c_flags'].remove("--cpp")
        # we want no-vla for only cxx, but it's also applied for C in IDE, thus we remove it
        project_data['tool_specific']['uvision5']['misc']['c_flags'].remove("--no_vla")
        project_data['tool_specific']['uvision5']['misc']['ld_flags'] = self.progen_flags['ld_flags']

        i = 0
        for macro in project_data['common']['macros']:
            # armasm does not like floating numbers in macros, timestamp to int
            if macro.startswith('MBED_BUILD_TIMESTAMP'):
                timestamp = macro[len('MBED_BUILD_TIMESTAMP='):]
                project_data['common']['macros'][i] = 'MBED_BUILD_TIMESTAMP=' + str(int(float(timestamp)))
            # armasm does not even accept MACRO=string
            if macro.startswith('MBED_USERNAME'):
                project_data['common']['macros'].pop(i)
            i += 1
        project_data['common']['macros'].append('__ASSERT_MSG')
        if progen_build:
            self.progen_gen_file('uvision5', project_data, True)
        else:
            self.progen_gen_file('uvision5', project_data)
