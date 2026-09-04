# Homelab repository guidance

## Scope

This repository manages physical hosts with Ansible, Proxmox VMs and Talos
bootstrap with Terraform/Terragrunt, and in-cluster workloads with Argo CD.
Keep changes declarative and make them in the layer that owns the resource.

## Work efficiently

- Start with `git status`, then inspect only files relevant to the task.
- Use [`agent_entry_point.md`](agent_entry_point.md) as the on-demand context
  map for cross-layer work. Do not read every runbook by default.
- Treat environment configs and executable manifests as more authoritative than
  summary documentation. Report inconsistencies before choosing which side to
  change.
- Keep `README.md` human-facing. Put operational detail in the focused runbook
  and durable agent rules in this file.

## Ownership and invariants

- Ansible owns physical Proxmox/Ubuntu configuration. Terraform owns VMs,
  Talos, cluster bootstrap, Cilium, and initial Argo CD. Argo CD owns resources
  under `kubernetes/`.
- `ubuntu-workstation` is the permanent critical-NFS host and local desktop, not
  a hypervisor. `smallgpu` and `largegpu` are borrowed and may hold only
  reproducible or disposable application data. `tinygpu` is a third Proxmox
  failure domain but has no declared storage, UPS, NFS, or VFIO role.
- Talos VM `402` and Windows VM `502` share the RTX 3080 and cannot run
  together.
- The three Talos control planes are fixed one per Proxmox failure domain:
  `cp-1` on `largegpu`, `cp-2` on `smallgpu`, and `cp-3` on `tinygpu`. Preserve
  the existing machine secrets and etcd data; never bootstrap the cluster
  again. One failed control-plane host is tolerated, but two failures lose etcd
  quorum.
- `tinygpu` prioritizes `cp-3`. Do not add a worker there without a new live
  capacity review; its 4-core i5-2500 and 11.63 GiB usable RAM do not currently
  justify sharing the host with another 4 GiB VM.
- Never partition, format, force-mount, or clear safety flags on existing disks.
- Preserve `Retain` behavior and the critical/bulk/scratch storage boundaries.

## Safety

- `.env` and `kubeconfig.yaml` are local, gitignored, and sensitive. Do not read
  or print them unless the user explicitly requests a task that needs live
  access. Their presence is not authorization to mutate infrastructure.
- Never commit credentials, access configs, private keys, state, plans, or
  generated secrets. `.env-template` may contain names and placeholders only.
- Do not apply/destroy Terraform, run mutating Ansible plays, apply Kubernetes
  resources, sync Argo CD, reboot hosts, or change VM power state unless the
  user explicitly asks for that live action.
- Preserve unrelated working-tree changes.

## Task routing

- Topology and sizing: `terraform/deployments/prod/config.yml`
- Shared versions/defaults: `terraform/deployments/config.yml`
- Talos/Cilium/Argo bootstrap: `terraform/modules/stacks/homelab-cluster/`
- Windows stack: `terraform/deployments/prod/windows-workstation/`
- Physical hosts: `ansible/README.md`, then the relevant playbook, host vars,
  and role
- Kubernetes apps: matching `kubernetes/apps/*.yaml`, then its
  `kubernetes/system/<name>/` resources
- Operational behavior: the matching focused file under `docs/`
- Architecture rationale only when needed: relevant section of
  `docs/architecture.md`

## Verification

Run the narrowest applicable checks and report anything that could not run:

- Terraform: `terraform fmt -check -recursive terraform/`
- Terragrunt: `terragrunt hclfmt --terragrunt-check --terragrunt-working-dir terraform/deployments`
- Shell: `bash -n terraform/run-terragrunt.sh terraform/deployments/merge_configs.sh`
- Ansible: run the affected playbook with `--syntax-check` from `ansible/`
- Kubernetes: validate the Application and every manifest at its source path;
  use client-side validation when available

Plans and live verification should be run only when relevant to the request.
Update focused documentation when behavior, topology, recovery steps, or safety
assumptions change.
