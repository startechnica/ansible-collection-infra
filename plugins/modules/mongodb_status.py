#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2026, firmansyahn
# GNU General Public License v3.0+

DOCUMENTATION = r'''
---
module: mongodb_status
short_description: Collect MongoDB sharded cluster status
description:
  - Checks container status for all MongoDB components.
  - Checks replica set status for shard and configsvr via mongosh.
  - Checks TLS certificate expiry dates.
  - Returns a consolidated status report.
version_added: "1.0.0"
author: firmansyahn (@firmansyahn)
options:
  containers:
    description: List of container names to check.
    type: list
    elements: str
    default:
      - mongodb-mongod
      - mongodb-configsvr
      - mongodb-mongos
  container_engine:
    description: Container runtime used to inspect/exec containers (docker or podman).
    type: str
    default: docker
    choices:
      - docker
      - podman
  replica_sets:
    description: >
      List of replica sets to check. Each entry is a dict with
      name, port, and optional type (shard or configsvr).
    type: list
    elements: dict
    required: true
  tls_certfile:
    description: Path to TLS certificate PEM file (inside container) for RS status checks.
    default: ""
    type: str
  tls_cafile:
    description: Path to CA certificate PEM file (inside container) for RS status checks.
    default: ""
    type: str
  mongodb_tls_dir:
    description: Host directory containing TLS certificates for expiry checks.
    default: ""
    type: str
  cert_files:
    description: List of certificate filenames in mongodb_tls_dir to check expiry.
    type: list
    elements: str
    default:
      - ca.pem
      - mongodb-mongod.crt
      - mongodb-configsvr.crt
      - mongodb-mongos.crt
'''

EXAMPLES = r'''
- name: Check MongoDB cluster status
  mongodb_status:
    replica_sets:
      - name: rs0
        port: 27019
        type: shard
      - name: configReplSet
        port: 27018
        type: configsvr
    tls_certfile: /etc/mongo/ssl/healthcheck.pem
    tls_cafile: /etc/mongo/ssl/ca.pem
    mongodb_tls_dir: /opt/mongodb/ssl
'''

RETURN = r'''
containers:
  description: Status of each container.
  returned: always
  type: dict
  sample: {"mongodb-mongod": "running", "mongodb-configsvr": "running"}
replica_sets:
  description: Status of each replica set.
  returned: always
  type: dict
  sample: {"rs0": {"ok": true, "primary": "10.0.0.1:27019", "members": 3}}
certificates:
  description: Certificate expiry info.
  returned: when mongodb_tls_dir is set
  type: dict
  sample: {"ca.pem": {"expires": "2027-04-19", "days_remaining": 365}}
healthy:
  description: Overall cluster health (all containers running, all RS have primary).
  returned: always
  type: bool
'''

import json
import os
import subprocess
from datetime import datetime
from ansible.module_utils.basic import AnsibleModule


def run_cmd(cmd, timeout=30):
    """Run a command and return (rc, stdout, stderr)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return 1, '', str(e)


def check_container(name, engine="docker"):
    """Check if a container is running. Returns status string."""
    rc, stdout, stderr = run_cmd([
        engine, "inspect", "--format", "{{.State.Status}}", name
    ])
    if rc == 0:
        return stdout
    return "not found"


def check_rs_status(container, port, tls_certfile, tls_cafile, engine="docker"):
    """Check replica set status via mongosh inside the container."""
    eval_str = (
        "JSON.stringify({"
        "ok: rs.status().ok,"
        "myState: rs.status().myState,"
        "members: rs.status().members.map(m => ({name: m.name, stateStr: m.stateStr}))"
        "})"
    )
    cmd = [engine, "exec", container, "mongosh", "--port", str(port), "--quiet"]
    if tls_certfile:
        cmd += ["--tls", "--tlsCertificateKeyFile", tls_certfile]
    if tls_cafile:
        cmd += ["--tlsCAFile", tls_cafile]
    cmd += ["--eval", eval_str]

    rc, stdout, stderr = run_cmd(cmd)
    if rc == 0 and stdout:
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith('{'):
                try:
                    data = json.loads(line)
                    members = data.get('members', [])
                    primary = next(
                        (m['name'] for m in members if m['stateStr'] == 'PRIMARY'),
                        None
                    )
                    return {
                        'ok': bool(data.get('ok')),
                        'primary': primary,
                        'members': len(members),
                        'detail': members,
                    }
                except (json.JSONDecodeError, ValueError):
                    pass
    return {
        'ok': False,
        'primary': None,
        'members': 0,
        'error': stderr or stdout or 'unknown error',
    }


def check_cert_expiry(cert_path):
    """Check certificate expiry using openssl."""
    rc, stdout, stderr = run_cmd([
        "openssl", "x509", "-in", cert_path, "-noout", "-enddate"
    ])
    if rc == 0 and stdout:
        # Format: notAfter=Apr 19 22:00:00 2027 GMT
        date_str = stdout.split('=', 1)[-1].strip()
        try:
            expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z")
            days_remaining = (expiry - datetime.utcnow()).days
            return {
                'expires': expiry.strftime("%Y-%m-%d"),
                'days_remaining': days_remaining,
            }
        except ValueError:
            return {'expires': date_str, 'days_remaining': -1}
    return {'error': stderr or 'unable to read certificate'}


def main():
    module = AnsibleModule(
        argument_spec=dict(
            containers=dict(
                type='list', elements='str',
                default=['mongodb-mongod', 'mongodb-configsvr', 'mongodb-mongos'],
            ),
            container_engine=dict(type='str', default='docker', choices=['docker', 'podman']),
            replica_sets=dict(type='list', elements='dict', required=True),
            tls_certfile=dict(type='str', default=''),
            tls_cafile=dict(type='str', default=''),
            mongodb_tls_dir=dict(type='str', default=''),
            cert_files=dict(
                type='list', elements='str',
                default=['ca.pem', 'mongodb-mongod.crt', 'mongodb-configsvr.crt', 'mongodb-mongos.crt'],
            ),
        ),
        supports_check_mode=True,
    )

    containers_list = module.params['containers']
    engine = module.params['container_engine']
    replica_sets = module.params['replica_sets']
    tls_certfile = module.params['tls_certfile']
    tls_cafile = module.params['tls_cafile']
    mongodb_tls_dir = module.params['mongodb_tls_dir']
    cert_files = module.params['cert_files']

    result = dict(
        changed=False,
        containers={},
        replica_sets={},
        certificates={},
        healthy=True,
    )

    # Check containers
    for name in containers_list:
        status = check_container(name, engine)
        result['containers'][name] = status
        if status != 'running':
            result['healthy'] = False

    # Check replica sets
    for rs in replica_sets:
        rs_name = rs.get('name', '')
        rs_port = rs.get('port', 27017)
        rs_type = rs.get('type', 'shard')
        container = 'mongodb-configsvr' if rs_type == 'configsvr' else 'mongodb-mongod'

        rs_status = check_rs_status(container, rs_port, tls_certfile, tls_cafile, engine)
        result['replica_sets'][rs_name] = rs_status
        if not rs_status.get('ok') or not rs_status.get('primary'):
            result['healthy'] = False

    # Check certificate expiry
    if mongodb_tls_dir:
        for cert_file in cert_files:
            cert_path = os.path.join(mongodb_tls_dir, cert_file)
            if os.path.isfile(cert_path):
                result['certificates'][cert_file] = check_cert_expiry(cert_path)
            else:
                result['certificates'][cert_file] = {'error': 'file not found'}

    # Build summary message
    container_summary = ", ".join(
        "%s=%s" % (k, v) for k, v in result['containers'].items()
    )
    rs_summary = ", ".join(
        "%s: primary=%s members=%d" % (k, v.get('primary', 'none'), v.get('members', 0))
        for k, v in result['replica_sets'].items()
    )
    result['msg'] = "Containers: [%s] | RS: [%s] | Healthy: %s" % (
        container_summary, rs_summary, result['healthy']
    )

    module.exit_json(**result)


if __name__ == '__main__':
    main()
