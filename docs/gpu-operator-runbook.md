# NVIDIA GPU Operator runbook

The production cluster has two GPU workers:

| Node | GPU | Scheduling role |
|------|-----|-----------------|
| `gpu-2` | GeForce RTX 3080 | Dedicated GPU worker; mutually exclusive with the Windows gaming VM |
| `gpu-3` | GeForce RTX 2060 | Mixed general-purpose and GPU worker |

## Ownership model

GPU enablement spans three independently managed layers:

1. Ansible configures IOMMU and VFIO on the two GPU-bearing Proxmox hosts.
2. Terraform passes the PCI device to the Talos VM and applies the GPU Talos
   machine configuration. Both nodes use the production open-driver image
   profile.
3. ArgoCD installs NVIDIA GPU Operator. The operator discovers GPU nodes,
   exposes `nvidia.com/gpu`, validates CUDA access, and exports DCGM metrics.

Talos owns the driver and toolkit. The GPU Operator Helm values therefore set:

```yaml
driver:
  enabled: false
toolkit:
  enabled: false
hostPaths:
  driverInstallDir: /usr/local
```

Do not enable the operator's driver or toolkit DaemonSets on Talos.

## Deploy

Commit and push the manifests. The root Application discovers
`kubernetes/apps/nvidia-gpu-operator.yaml` automatically. To bootstrap it
directly before the next root sync:

```bash
kubectl apply -f kubernetes/apps/nvidia-gpu-operator.yaml
```

Watch reconciliation:

```bash
kubectl -n argocd get application nvidia-gpu-operator -w
kubectl -n gpu-operator get pods -o wide -w
```

The namespace deliberately enforces the privileged Pod Security profile because
the device plugin, validators, node-feature-discovery, and DCGM need host access.

## Verify all GPUs

Check that all GPU nodes advertise one schedulable GPU:

```bash
kubectl get nodes \
  -o custom-columns='NODE:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu'
```

Expected:

```text
cp-1    <none>
gpu-2   1
gpu-3   1
```

Check the operator state and discovery labels:

```bash
kubectl get clusterpolicy
kubectl get nodes -L nvidia.com/gpu.present,nvidia.com/gpu.product
```

Run one CUDA validation pod per GPU node:

```bash
for node in gpu-2 gpu-3; do
  kubectl run "nvidia-smi-${node}" \
    --restart=Never \
    --image=nvcr.io/nvidia/cuda:12.9.1-base-ubuntu24.04 \
    --overrides="$(
      printf '{"spec":{"nodeName":"%s","restartPolicy":"Never","containers":[{"name":"nvidia-smi","image":"nvcr.io/nvidia/cuda:12.9.1-base-ubuntu24.04","command":["nvidia-smi"],"resources":{"limits":{"nvidia.com/gpu":1}}}]}}' "$node"
    )"
done

kubectl logs -f pod/nvidia-smi-gpu-2
kubectl logs -f pod/nvidia-smi-gpu-3
kubectl delete pod nvidia-smi-gpu-2 nvidia-smi-gpu-3
```

These are explicit validation pods. Normal workloads should use node affinity
only when they truly require a particular GPU; otherwise request
`nvidia.com/gpu: 1` and let the scheduler choose an available node.

## Prometheus and Grafana

GPU Operator enables DCGM Exporter and creates a `ServiceMonitor` labeled for
the `monitoring` Prometheus release. Verify the targets:

```bash
kubectl -n monitoring port-forward \
  svc/monitoring-kube-prometheus-prometheus 9090:9090
```

Open `http://localhost:9090/targets` and look for the DCGM exporter target.
Useful metrics include:

```promql
DCGM_FI_DEV_GPU_UTIL
DCGM_FI_DEV_FB_USED
DCGM_FI_DEV_GPU_TEMP
DCGM_FI_DEV_POWER_USAGE
```

## `gpu-2` and Windows

The RTX 3080 is passed through to either Talos VM `402` or Windows VM `502`.
They cannot run concurrently. Drain `gpu-2` before switching to Windows:

```bash
kubectl drain gpu-2 --ignore-daemonsets --delete-emptydir-data
ssh root@192.168.1.107 'qm shutdown 402 && qm start 502'
```

When returning the GPU to Kubernetes:

```bash
ssh root@192.168.1.107 'qm shutdown 502 && qm start 402'
kubectl uncordon gpu-2
```

GPU workloads must tolerate eviction and be able to restart on `gpu-3`. A
workload requesting a GPU cannot move until the RTX 2060 is free.
