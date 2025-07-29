# Kubernetes Admission Webhook System

A comprehensive FastAPI-based Kubernetes admission webhook system that provides both mutating and validating capabilities for Pod CPU requests and node selector requirements.

## 🚀 Quick Start (TL;DR)

```bash
# 1. Build and deploy everything
make deploy

# 2. Test the webhook
./test-webhook.sh

# 3. View logs
make logs

# 4. Clean up when done
make clean
```

## Features

### 🔄 Mutating Webhook (`/mutate`)
- **Standalone Pods**: Sets CPU request to 500m
- **ReplicaSet/Deployment Pods**: Assigns random CPU requests between 100m-500m
- **Smart Exclusion**: Automatically excludes webhook pods from mutation
- **Randomization**: Each ReplicaSet/Deployment pod gets a unique random CPU value

### 🛡️ Validating Webhook (`/validate`)
- **CPU Threshold Protection**: Blocks pod deletion if total remaining CPU in ReplicaSet falls below threshold
- **Node Selector Protection**: Ensures minimum number of pods with specific node selector
- **Standalone Pod Freedom**: Allows deletion of non-ReplicaSet pods
- **ReplicaSet Awareness**: Only validates pods owned by ReplicaSets

### ⚙️ Configurable Parameters
- **CPU Threshold**: Configurable via `CPU_THRESHOLD_MILLICORES` environment variable (default: 1000m)
- **Node Selector Requirements**: Configurable via environment variables
- **Zero Downtime**: Configuration changes applied via environment variables without image rebuilds

## Prerequisites

- **Minikube**: Running Kubernetes cluster
- **kubectl**: Configured to access the cluster
- **Docker**: For building the webhook image
- **OpenSSL**: For TLS certificate generation

## Quick Start Guide

### 1. Setup Environment

```bash
# Start minikube (if not running)
minikube start

# Clone the repository
git clone <repository>
cd groundcover-test
```

### 2. Build and Deploy

```bash
# Build the Docker image for minikube
eval $(minikube docker-env)
docker build -f Dockerfile.custom -t pod-cpu-mutator:latest .

# Generate TLS certificates and create secret
./generate-certs.sh

# Deploy all components
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
kubectl apply -f mutating-webhook-with-ca.yaml
kubectl apply -f validating-webhook.yaml
```

### 3. Verify Deployment

```bash
# Check webhook pod is running
kubectl get pods -l app=pod-cpu-mutator

# Check webhook logs for startup messages
kubectl logs -l app=pod-cpu-mutator
```

**Expected log output:**
```
INFO:__main__:Starting Pod CPU Webhook on port 8443
INFO:__main__:CPU threshold set to 1000m (1.0 CPUs)
INFO:__main__:At least 2 pods must have node-type=on_demand
INFO:     Uvicorn running on https://0.0.0.0:8443
```

## Configuration

### Environment Variables

Configure webhook behavior in `deployment.yaml`:

```yaml
env:
- name: CPU_THRESHOLD_MILLICORES
  value: "1000"         # Minimum CPU to keep in ReplicaSet
- name: MIN_PODS_WITH_SELECTOR
  value: "2"            # Minimum pods with required selector
- name: REQUIRED_NODE_SELECTOR_KEY
  value: "node-type"    # Node selector key to monitor
- name: REQUIRED_NODE_SELECTOR_VALUE
  value: "on_demand"    # Required node selector value
```

### Configuration Examples

#### Production (High Availability)
```yaml
env:
- name: CPU_THRESHOLD_MILLICORES
  value: "4000"  # 4 CPUs minimum
- name: MIN_PODS_WITH_SELECTOR
  value: "3"     # 3 critical pods minimum
```

#### Development (Minimal Resources)
```yaml
env:
- name: CPU_THRESHOLD_MILLICORES
  value: "500"   # 0.5 CPU minimum
- name: MIN_PODS_WITH_SELECTOR
  value: "1"     # 1 critical pod minimum
```

### Apply Configuration Changes

```bash
# Update environment variables without rebuilding
kubectl patch deployment pod-cpu-mutator -p '{
  "spec": {
    "template": {
      "spec": {
        "containers": [{
          "name": "pod-cpu-mutator",
          "env": [
            {"name": "CPU_THRESHOLD_MILLICORES", "value": "2000"},
            {"name": "MIN_PODS_WITH_SELECTOR", "value": "3"}
          ]
        }]
      }
    }
  }
}'

# Restart to apply changes
kubectl rollout restart deployment/pod-cpu-mutator
```

## Testing Guide

### Automated Testing

Run the comprehensive test script to verify all features:

```bash
# Run all tests automatically
./test-webhook.sh
```

**The test script validates:**
- ✅ Mutating webhook intercepts pod creation
- ✅ CPU requests are correctly assigned (500m for standalone, 100m-500m for ReplicaSet)
- ✅ Validating webhook blocks pod deletion when CPU threshold would be violated
- ✅ Validating webhook blocks pod deletion when node selector requirements would be violated
- ✅ Configuration changes work without rebuilding images

### Manual Testing

#### 1. Test Mutating Webhook

**Test Standalone Pod:**
```bash
# Create a standalone pod
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: test-standalone
spec:
  containers:
  - name: app
    image: nginx
    resources:
      requests:
        cpu: "100m"  # This will be changed to 500m
EOF

# Verify CPU was mutated to 500m
kubectl get pod test-standalone -o jsonpath='{.spec.containers[0].resources.requests.cpu}'
# Expected: 500m

# Clean up
kubectl delete pod test-standalone
```

**Test ReplicaSet Pod:**
```bash
# Create a deployment
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-deployment
spec:
  replicas: 3
  selector:
    matchLabels:
      app: test-app
  template:
    metadata:
      labels:
        app: test-app
    spec:
      containers:
      - name: app
        image: nginx
        resources:
          requests:
            cpu: "100m"  # This will be randomized between 100m-500m
EOF

# Verify each pod has different random CPU values
kubectl get pods -l app=test-app -o jsonpath='{range .items[*]}{.metadata.name}: {.spec.containers[0].resources.requests.cpu}{"\n"}{end}'

# Clean up
kubectl delete deployment test-deployment
```

#### 2. Test Validating Webhook

**Test CPU Threshold Protection:**
```bash
# Create a deployment with sufficient CPU
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cpu-test
spec:
  replicas: 5
  selector:
    matchLabels:
      app: cpu-test
  template:
    metadata:
      labels:
        app: cpu-test
    spec:
      containers:
      - name: app
        image: nginx
        resources:
          requests:
            cpu: "300m"  # Will be randomized, but total should be ~1000m+
EOF

# Wait for pods to be created
kubectl wait --for=condition=Ready pods -l app=cpu-test --timeout=60s

# Try to delete a pod - should be blocked if total CPU would drop below threshold
kubectl delete pod -l app=cpu-test --timeout=10s
# Expected: Should be blocked with message about CPU threshold
```

**Test Node Selector Protection:**
```bash
# Label a node for testing
kubectl label nodes $(kubectl get nodes -o jsonpath='{.items[0].metadata.name}') node-type=on_demand

# Create deployment with node selector
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: selector-test
spec:
  replicas: 3
  selector:
    matchLabels:
      app: selector-test
  template:
    metadata:
      labels:
        app: selector-test
    spec:
      nodeSelector:
        node-type: on_demand
      containers:
      - name: app
        image: nginx
        resources:
          requests:
            cpu: "200m"
EOF

# Wait for pods
kubectl wait --for=condition=Ready pods -l app=selector-test --timeout=60s

# Try to delete pods - should be blocked if it would drop below MIN_PODS_WITH_SELECTOR
kubectl delete pod -l app=selector-test --timeout=10s
# Expected: Should be blocked with message about node selector requirements
```

#### 3. Test Configuration Changes

```bash
# Change CPU threshold to 2000m without rebuilding
kubectl patch deployment pod-cpu-mutator -p '{"spec":{"template":{"spec":{"containers":[{"name":"pod-cpu-mutator","env":[{"name":"CPU_THRESHOLD_MILLICORES","value":"2000"}]}]}}}}'

# Restart to apply changes
kubectl rollout restart deployment/pod-cpu-mutator
kubectl rollout status deployment/pod-cpu-mutator

# Check logs to confirm new threshold
kubectl logs -l app=pod-cpu-mutator | grep "CPU threshold"
# Expected: "INFO:__main__:CPU threshold set to 2000m"
```

### Health Checks

```bash
# Check readiness endpoint
kubectl port-forward service/pod-cpu-mutator-service 8443:443 &
sleep 2
curl -k https://localhost:8443/ready
# Expected: {"status": "ready"}

# Kill port-forward
pkill -f "kubectl port-forward"
```

## Troubleshooting

### Common Issues

#### 1. Webhook Not Intercepting Pods
**Symptoms:** Pods created with original CPU requests, not mutated
**Solutions:**
```bash
# Check webhook registration
kubectl get mutatingadmissionwebhook pod-cpu-mutator-webhook -o yaml

# Verify webhook pod is running
kubectl get pods -l app=pod-cpu-mutator

# Check webhook logs
kubectl logs -l app=pod-cpu-mutator -f

# Test webhook connectivity
kubectl exec -it deploy/pod-cpu-mutator -- curl -k https://localhost:8443/ready
```

#### 2. Certificate Issues
**Symptoms:** TLS handshake errors, webhook timeouts
**Solutions:**
```bash
# Regenerate certificates
kubectl delete secret pod-cpu-mutator-certs
./generate-certs.sh

# Restart webhook
kubectl rollout restart deployment/pod-cpu-mutator

# Verify secret exists
kubectl get secret pod-cpu-mutator-certs -o yaml
```

#### 3. Pod Deletion Not Blocked
**Symptoms:** Pods delete despite threshold violations
**Solutions:**
```bash
# Check validating webhook registration
kubectl get validatingadmissionwebhook pod-cpu-mutator-validating -o yaml

# Verify webhook receives DELETE requests
kubectl logs -l app=pod-cpu-mutator | grep DELETE

# Check current threshold configuration
kubectl logs -l app=pod-cpu-mutator | grep "CPU threshold"
```

#### 4. Configuration Not Applied
**Symptoms:** Old configuration still active after changes
**Solutions:**
```bash
# Force restart deployment
kubectl rollout restart deployment/pod-cpu-mutator

# Verify environment variables
kubectl get deployment pod-cpu-mutator -o jsonpath='{.spec.template.spec.containers[0].env}'

# Check logs for new configuration
kubectl logs -l app=pod-cpu-mutator --tail=20
```

### Debug Commands

```bash
# Comprehensive status check
echo "=== Webhook Pod Status ==="
kubectl get pods -l app=pod-cpu-mutator

echo "=== Webhook Logs ==="
kubectl logs -l app=pod-cpu-mutator --tail=10

echo "=== Webhook Registration ==="
kubectl get mutatingadmissionwebhook pod-cpu-mutator-webhook
kubectl get validatingadmissionwebhook pod-cpu-mutator-validating

echo "=== Service Status ==="
kubectl get service pod-cpu-mutator-service
kubectl get endpoints pod-cpu-mutator-service

echo "=== Certificate Secret ==="
kubectl get secret pod-cpu-mutator-certs
```

### Performance Monitoring

```bash
# Monitor webhook response times
kubectl logs -l app=pod-cpu-mutator | grep "Processing"

# Check webhook resource usage
kubectl top pods -l app=pod-cpu-mutator

# Monitor API server webhook calls
kubectl get events --field-selector involvedObject.kind=Pod | grep admission
```

## How It Works

### Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────────┐
│   API Server    │───▶│  Admission       │───▶│    Pod CPU          │
│                 │    │  Controllers     │    │    Webhook          │
│                 │    │                  │    │                     │
│  - Create Pod   │    │ - Mutating       │    │ - /mutate endpoint  │
│  - Delete Pod   │    │ - Validating     │    │ - /validate endpoint│
└─────────────────┘    └──────────────────┘    └─────────────────────┘
                                 │
                                 ▼
                       ┌──────────────────┐
                       │   Kubernetes     │
                       │     Cluster      │
                       │                  │
                       │ - Apply patches  │
                       │ - Create/Delete  │
                       │   pods           │
                       └──────────────────┘
```

### Webhook Flow

1. **Pod Creation (Mutating)**:
   - API Server intercepts Pod creation
   - Sends AdmissionReview to `/mutate` endpoint
   - Webhook analyzes pod ownership and generates CPU patches
   - API Server applies patches and creates modified pod

2. **Pod Deletion (Validating)**:
   - API Server intercepts Pod deletion request
   - Sends AdmissionReview to `/validate` endpoint  
   - Webhook checks CPU threshold and node selector requirements
   - Returns admission decision (allow/deny)

### CPU Assignment Logic

```python
def determine_cpu_request(pod_spec, owner_references):
    if not owner_references:
        return "500m"  # Standalone pod
    
    for owner in owner_references:
        if owner.kind in ["ReplicaSet", "Deployment"]:
            return f"{random.randint(100, 500)}m"  # Random for RS/Deploy
    
    return "500m"  # Default
```

### Validation Logic

```python
def validate_deletion(pod, namespace):
    # Allow standalone pod deletion
    if not has_replicaset_owner(pod):
        return True
    
    # Check CPU threshold
    if total_remaining_cpu < threshold:
        deny_with_message("CPU threshold violation")
    
    # Check node selector requirements  
    if pods_with_selector < minimum_required:
        deny_with_message("Node selector requirement violation")
    
    return True  # Allow deletion
```

## Cleanup

### Remove Webhook System

```bash
# Remove webhook configurations (stops interception)
kubectl delete mutatingadmissionwebhook pod-cpu-mutator-webhook
kubectl delete validatingadmissionwebhook pod-cpu-mutator-validating

# Remove application and service
kubectl delete deployment pod-cpu-mutator
kubectl delete service pod-cpu-mutator-service

# Remove certificates
kubectl delete secret pod-cpu-mutator-certs

# Clean up test resources
kubectl delete deployment test-deployment cpu-test selector-test --ignore-not-found
kubectl delete pod test-standalone --ignore-not-found
```

### Remove Node Labels

```bash
# Remove test node labels
kubectl label nodes --all node-type-
```

## Security Considerations

- **TLS Encryption**: All webhook communication uses HTTPS with proper certificates
- **Fail-Safe Design**: If webhook is unavailable, operations fail securely (failurePolicy: Fail)
- **Namespace Isolation**: Webhook operates across all namespaces but can be restricted
- **RBAC Ready**: Can be enhanced with specific ServiceAccounts and ClusterRoles
- **Certificate Management**: Uses Kubernetes-native secret storage
- **No Privilege Escalation**: Webhook runs with minimal required permissions

## Make Commands

For convenience, use the provided Makefile commands:

```bash
# Build Docker image for minikube
make build

# Generate TLS certificates
make certs

# Deploy everything (build + certs + deploy)
make deploy

# Run basic functionality test
make test

# View webhook logs
make logs

# Check deployment status
make status

# View webhook configuration
make webhook-config

# Clean up all resources
make clean

# Full development cycle (clean + build + deploy + test)
make dev
```

## Project Structure

```
groundcover-test/
├── app.py                          # Main FastAPI webhook application
├── requirements.txt                # Python dependencies
├── Dockerfile.custom              # Docker build configuration
├── Makefile                       # Build and deployment commands
├── generate-certs.sh              # TLS certificate generation script
├── test-webhook.sh                # Comprehensive test script
├── deployment.yaml                # Kubernetes deployment configuration
├── service.yaml                   # Kubernetes service configuration
├── mutating-webhook-with-ca.yaml  # Mutating webhook registration
├── validating-webhook.yaml        # Validating webhook registration
├── test-deployment.yaml           # Example test deployment
└── README.md                      # This documentation
```

### Key Files

- **`app.py`**: Core webhook logic with `/mutate` and `/validate` endpoints
- **`deployment.yaml`**: Contains environment variable configuration
- **`generate-certs.sh`**: Creates TLS certificates and Kubernetes secret
- **`test-webhook.sh`**: Automated testing for all webhook features
- **`Makefile`**: Convenient commands for building, deploying, and testing

## Extending the Webhook

### Adding New Mutation Rules

```python
# In app.py, extend the mutate_pod function
def mutate_pod(pod_spec, metadata):
    patches = []
    
    # Existing CPU logic
    patches.extend(generate_cpu_patches(pod_spec, metadata))
    
    # Add new mutation (example: memory requests)
    patches.extend(generate_memory_patches(pod_spec, metadata))
    
    return patches
```

### Adding New Validation Rules

```python
# In app.py, extend the validate_pod_deletion function  
def validate_pod_deletion(pod, namespace):
    # Existing validations
    if not validate_cpu_threshold(pod, namespace):
        return False, "CPU threshold violation"
    
    if not validate_node_selector(pod, namespace):
        return False, "Node selector violation"
    
    # Add new validation (example: storage requirements)
    if not validate_storage_requirements(pod, namespace):
        return False, "Storage requirement violation"
    
    return True, "Allowed"
```

### Configuration Parameters

Add new environment variables to `deployment.yaml`:

```yaml
env:
- name: MEMORY_THRESHOLD_MI
  value: "2048"  # Example: memory threshold
- name: REQUIRED_STORAGE_CLASS
  value: "fast-ssd"  # Example: storage requirement
```

### 🚀 Quick Commands Reference

```bash
# Deploy everything
make deploy

# Test all features  
./test-webhook.sh

# Monitor webhook
make logs

# Change configuration (example)
kubectl patch deployment pod-cpu-mutator -p '{"spec":{"template":{"spec":{"containers":[{"name":"pod-cpu-mutator","env":[{"name":"CPU_THRESHOLD_MILLICORES","value":"2000"}]}]}}}}'

# Clean up
make clean
```
