# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Defines neural_structured_learning version information."""

# We follow Semantic Versioning (https://semver.org/).
_MAJOR_VERSION = '1'
_MINOR_VERSION = '0'
_PATCH_VERSION = '1'

_VERSION_SUFFIX = ''

__version__ = '.'.join([_MAJOR_VERSION, _MINOR_VERSION, _PATCH_VERSION])
if _VERSION_SUFFIX:
  __version__ = '{}-{}'.format(__version__, _VERSION_SUFFIX)
