#!/usr/bin/env python3
"""Static provider/product boundary; never reads access files or contacts a cluster."""
from pathlib import Path
import re
import subprocess
import yaml

root = Path(__file__).resolve().parents[1]
app = yaml.safe_load((root / 'kubernetes/apps/tapy.yaml').read_text())
assert app['spec']['source']['path'] == 'deploy/kubernetes/overlays/homelab'
assert 'homelab.git' not in app['spec']['source']['repoURL']
assert 'automated' not in app['spec'].get('syncPolicy', {})
assert not app['metadata'].get('finalizers')
assert app['metadata']['annotations']['argocd.argoproj.io/sync-options'] == 'Prune=false,Delete=false'
tracked = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root).decode().split('\0')
# git still lists deleted files until the review is staged.
assert not any((root/p).is_file() for p in tracked if p.startswith(('services/tapy/', 'kubernetes/system/tapy/')))
for base in ['services/job-assistant', 'services/internal-llm', 'kubernetes/system/job-assistant', 'kubernetes/system/internal-llm', 'kubernetes/system/homelab-assistant']:
    for name in tracked:
        if not name.startswith(base + '/') or not (root/name).is_file():
            continue
        text = (root/name).read_text(errors='replace')
        assert not re.search(r'tapy-rabbitmq|tapy-external-ai|(?:rabbitmq|external-ai)\.tapy\.svc', text), f'foreign Tapy dependency: {name}'
admission = yaml.safe_load((root/'kubernetes/system/cloudflared/tapy-admission.yaml').read_text())
assert admission['metadata']['namespace'] == 'tapy'
assert admission['spec']['podSelector']['matchLabels'] == {'app':'tapy','component':'frontend'}
assert admission['spec']['ingress'][0]['ports'] == [{'port':3000,'protocol':'TCP'}]
for path in ['kubernetes/system/external-ai', 'kubernetes/system/rabbitmq', 'services/external-ai']:
    assert (root/path).is_dir(), f'missing independent homelab service: {path}'
print('Homelab/Tapy ownership boundary passed.')
