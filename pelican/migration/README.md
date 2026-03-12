<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->
# Migrating from Infrastructure Pelican to GitHub Actions

## Migration instructions
https://cwiki.apache.org/confluence/display/INFRA/Moving+from+Infrastructure-pelican+to+GitHub+Actions

## Template and GHA Workflow file
The build-pelican.yml.ezt and pelican.auto.ezt templates in this directory are used by the generate_settings.py script. 
The build-pelican.yml workflow may be used directly by projects wishing to use the pelican workflow.

## Updating the workflow

Before using the workflow file, ensure that the source and target branches are correct. otherwise you *could* commit to a production branch
