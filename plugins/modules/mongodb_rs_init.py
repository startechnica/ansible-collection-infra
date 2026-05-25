#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2026, firmansyahn
# GNU General Public License v3.0+

DOCUMENTATION = r'''
---
module: mongodb_rs_init
short_description: Initialize a MongoDB replica set via docker exec (localhost exception)
description:
  - Initializes a MongoDB replica set by running rs.initiate() inside a Docker container.
  - Uses the MongoDB localhost exception for authentication (no user/password needed).
  - Handles the post-initiate election race condition gracefully.
  - Idempotent — skips if the replica set is already initialized.
  - Waits for a primary to be elected before returning.
version_added: "1.0.0"
author: firmansyahn (@firmansyahn)
options:
  container:
    description: Docker container name running mongod/mongos.
    required: true
    type: str
  port:
    description: MongoDB port inside the container.
    default: 27017
    type: int
  replica_set:
    description: Replica set name.
    required: true
    type: str
  members:
    description: List of member addresses (host:port).
    required: true
    type: list
    elements: str
  configsvr:
    description: Whether this is a config server replica set.
    default: false
    type: bool
  tls_certfile:
    description: Path to TLS certificate+key PEM file inside the container.
    default: ""
    type: str
  tls_cafile:
    description: Path to CA certificate PEM file inside the container.
    default: ""
    type: str
  verify_retries:
    description: Number of retries to verify initialization succeeded.
    default: 30
    type: int
  verify_delay:
    description: Delay in seconds between verification retries.
    default: 5
    type: int
  primary_retries:
    description: Number of retries to wait for primary election.
    default: 30
    type: int
  primary_delay:
    description: Delay in seconds between primary election checks.
    default: 2
    type: int
'''

EXAMPLES = r'''
- name: Initialize configReplSet
  startechnica.infra.mongodb_rs_init:
    container: mongodb-configsvr
    replica_set: configReplSet
    configsvr: true
    members:
      - "10.147.8.56:27018"
      - "10.147.8.57:27018"
      - "10.147.8.58:27018"
    tls_certfile: /etc/mongo/ssl/healthcheck.pem
    tls_cafile: /etc/mongo/ssl/ca.pem

- name: Initialize shard replica set
  startechnica.infra.mongodb_rs_init:
    container: mongodb-mongod
    replica_set: rs0
    members:
      - "10.147.8.56:27019"
      - "10.147.8.57:27019"
      - "10.147.8.58:27019"
    tls_certfile: /etc/mongo/ssl/healthcheck.pem
    tls_cafile: /etc/mongo/ssl/ca.pem
'''

RETURN = r'''
initialized:
  description: Whether rs.initiate() was actually called (false if already initialized).
  returned: always
  type: bool
primary:
  description: The member that became primary.
  returned: success
  type: str
status:
  description: Final rs.status() output summary.
  returned: success
  type: dict
'''

import json
import subprocess
import time
from ansible.module_utils.basic import AnsibleModule


def docker_exec(container, command):
    """Execute a command inside a Docker container, return (rc, stdout, stderr)."""
    cmd = ["docker", "exec", container] + command
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def build_mongosh_cmd(port, tls_certfile, tls_cafile, eval_str):
    """Build a mongosh command with optional TLS args."""
    cmd = ["mongosh", "--port", str(port), "--quiet"]
    if tls_certfile:
        cmd += ["--tls", "--tlsCertificateKeyFile", tls_certfile]
    if tls_cafile:
        cmd += ["--tlsCAFile", tls_cafile]
    cmd += ["--eval", eval_str]
    return cmd


def check_rs_status(container, port, tls_certfile, tls_cafile):
    """Check if replica set is initialized. Returns (ok, myState, error_msg)."""
    cmd = build_mongosh_cmd(port, tls_certfile, tls_cafile,
                            "JSON.stringify({ok: rs.status().ok, myState: rs.status().myState})")
    rc, stdout, stderr = docker_exec(container, cmd)
    if rc == 0 and stdout:
        try:
            # mongosh may print warnings before JSON — find the JSON line
            for line in stdout.splitlines():
                line = line.strip()
                if line.startswith('{'):
                    data = json.loads(line)
                    return data.get("ok", 0), data.get("myState", 0), None
        except (json.JSONDecodeError, ValueError):
            pass
    error_detail = stderr if stderr else stdout
    return 0, 0, error_detail


def main():
    module = AnsibleModule(
        argument_spec=dict(
            container=dict(type='str', required=True),
            port=dict(type='int', default=27017),
            replica_set=dict(type='str', required=True),
            members=dict(type='list', elements='str', required=True),
            configsvr=dict(type='bool', default=False),
            tls_certfile=dict(type='str', default=''),
            tls_cafile=dict(type='str', default=''),
            verify_retries=dict(type='int', default=30),
            verify_delay=dict(type='int', default=5),
            primary_retries=dict(type='int', default=30),
            primary_delay=dict(type='int', default=2),
        ),
        supports_check_mode=True,
    )

    container = module.params['container']
    port = module.params['port']
    replica_set = module.params['replica_set']
    members = module.params['members']
    configsvr = module.params['configsvr']
    tls_certfile = module.params['tls_certfile']
    tls_cafile = module.params['tls_cafile']
    verify_retries = module.params['verify_retries']
    verify_delay = module.params['verify_delay']
    primary_retries = module.params['primary_retries']
    primary_delay = module.params['primary_delay']

    result = dict(changed=False, initialized=False, primary='', status={})

    # Step 1: Check if already initialized
    ok, my_state, err = check_rs_status(container, port, tls_certfile, tls_cafile)
    if ok == 1:
        result['msg'] = "Replica set '%s' already initialized" % replica_set
        result['status'] = {'ok': ok, 'myState': my_state}
        module.exit_json(**result)

    if module.check_mode:
        result['changed'] = True
        result['msg'] = "Would initialize replica set '%s'" % replica_set
        module.exit_json(**result)

    # Step 2: Build and fire rs.initiate() — capture output for diagnostics
    member_list = ", ".join(
        ["{_id: %d, host: '%s'}" % (i, m) for i, m in enumerate(members)]
    )
    config_parts = ["_id: '%s'" % replica_set]
    if configsvr:
        config_parts.append("configsvr: true")
    config_parts.append("members: [%s]" % member_list)

    initiate_js = "rs.initiate({%s})" % ", ".join(config_parts)
    cmd = build_mongosh_cmd(port, tls_certfile, tls_cafile, initiate_js)

    # Fire and forget — connection may drop during election
    init_rc, init_stdout, init_stderr = 0, '', ''
    try:
        init_rc, init_stdout, init_stderr = docker_exec(container, cmd)
    except Exception as e:
        init_stderr = str(e)  # Expected — election kills the connection

    # Step 3: Verify initialization succeeded (retry with reconnection)
    last_err = None
    for attempt in range(verify_retries):
        time.sleep(verify_delay)
        ok, my_state, last_err = check_rs_status(container, port, tls_certfile, tls_cafile)
        if ok == 1:
            break
    else:
        module.fail_json(
            msg="Replica set '%s' failed to initialize after %d attempts" % (replica_set, verify_retries),
            initiate_rc=init_rc,
            initiate_stdout=init_stdout,
            initiate_stderr=init_stderr,
            last_status_error=last_err or 'unknown',
        )

    result['changed'] = True
    result['initialized'] = True

    # Step 4: Wait for primary election
    for attempt in range(primary_retries):
        ok, my_state, _ = check_rs_status(container, port, tls_certfile, tls_cafile)
        if my_state == 1:  # PRIMARY
            result['primary'] = 'self'
            break
        if my_state == 2:  # SECONDARY — primary is elsewhere, that's fine
            result['primary'] = 'other'
            break
        time.sleep(primary_delay)
    else:
        module.fail_json(msg="No primary elected for '%s' after %d attempts"
                         % (replica_set, primary_retries))

    result['status'] = {'ok': ok, 'myState': my_state}
    result['msg'] = "Replica set '%s' initialized, primary: %s" % (replica_set, result['primary'])
    module.exit_json(**result)


if __name__ == '__main__':
    main()
