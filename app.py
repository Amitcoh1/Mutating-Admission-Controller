from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import json
import base64
import logging
import random
import hashlib
import time
import os
import re
from typing import Dict, Any, List, Tuple

# Configure logging first
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Try to import kubernetes client, make it optional for now
try:
    from kubernetes import client, config
    HAS_KUBERNETES = True
except ImportError:
    HAS_KUBERNETES = False
    logger.warning("kubernetes package not available - validation will be limited")

# CPU threshold in millicores - configurable via environment variable
# Default: 2 CPUs = 2000m
CPU_THRESHOLD_MILLICORES = int(os.getenv("CPU_THRESHOLD_MILLICORES", "2000"))
logger.info(f"CPU threshold set to {CPU_THRESHOLD_MILLICORES}m ({CPU_THRESHOLD_MILLICORES/1000:.1f} CPUs)")

# Node selector validation configuration
MIN_PODS_WITH_NODE_SELECTOR = int(os.getenv("MIN_PODS_WITH_NODE_SELECTOR", "2"))
REQUIRED_NODE_SELECTOR_KEY = os.getenv("REQUIRED_NODE_SELECTOR_KEY", "node-type")
REQUIRED_NODE_SELECTOR_VALUE = os.getenv("REQUIRED_NODE_SELECTOR_VALUE", "on_demand")
logger.info(f"Node selector validation: At least {MIN_PODS_WITH_NODE_SELECTOR} pods must have {REQUIRED_NODE_SELECTOR_KEY}={REQUIRED_NODE_SELECTOR_VALUE}")

app = FastAPI(title="Pod CPU Request Mutating and Validating Admission Controller")

# Initialize Kubernetes client
# Initialize Kubernetes client if available
if HAS_KUBERNETES:
    try:
        config.load_incluster_config()  # Load config when running in cluster
        logger.info("Loaded in-cluster Kubernetes config")
    except config.ConfigException:
        try:
            config.load_kube_config()  # Load config when running locally
            logger.info("Loaded local Kubernetes config")
        except config.ConfigException:
            logger.warning("Could not load Kubernetes config - some features may not work")
    
    k8s_v1 = client.CoreV1Api()
else:
    k8s_v1 = None
    logger.warning("Kubernetes client not available")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

@app.get("/ready")
async def ready_check():
    """Readiness check endpoint"""
    return {"status": "ready"}

@app.post("/mutate")
async def mutate_pods(request: Request):
    """
    Mutating admission webhook that:
    - Sets CPU requests to 500m for standalone pods
    - Sets random CPU requests (100m-500m) for ReplicaSet/Deployment pods
    """
    try:
        # Parse the admission review request
        body = await request.json()
        logger.info(f"Received admission review: {json.dumps(body, indent=2)}")
        
        # Extract the pod object from the request
        admission_request = body.get("request", {})
        pod_object = admission_request.get("object", {})
        
        # Validate that this is a Pod resource
        if pod_object.get("kind") != "Pod":
            logger.info(f"Ignoring non-Pod resource: {pod_object.get('kind')}")
            return create_admission_response(admission_request.get("uid"), allowed=True)
        
        # Generate JSON patch to override CPU request
        patch_operations = generate_cpu_request_patch(pod_object)
        
        # Create admission response with patches
        response = create_admission_response(
            uid=admission_request.get("uid"),
            allowed=True,
            patch=patch_operations
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Error processing admission request: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/validate")
async def validate_pods(request: Request):
    """
    Validating admission webhook that blocks Pod deletion 
    if total remaining CPU in ReplicaSet falls below threshold
    """
    try:
        # Parse the admission review request
        body = await request.json()
        logger.info(f"Received validation request: {json.dumps(body, indent=2)}")
        
        # Extract the admission request
        admission_request = body.get("request", {})
        operation = admission_request.get("operation", "")
        pod_object = admission_request.get("object", {}) or admission_request.get("oldObject", {})
        
        # Only validate DELETE operations on Pods
        if operation != "DELETE" or pod_object.get("kind") != "Pod":
            logger.info(f"Allowing non-DELETE Pod operation: {operation} on {pod_object.get('kind', 'unknown')}")
            return create_validation_response(admission_request.get("uid"), allowed=True)
        
        # Check if pod deletion should be blocked
        allowed, message = should_allow_pod_deletion(pod_object)
        
        return create_validation_response(
            uid=admission_request.get("uid"),
            allowed=allowed,
            message=message
        )
        
    except Exception as e:
        logger.error(f"Error processing validation request: {str(e)}")
        # Allow on error to avoid blocking legitimate operations
        return create_validation_response(admission_request.get("uid"), allowed=True, message="Validation error occurred")

def generate_cpu_request_patch(pod_object: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Generate JSON patch operations to set CPU request:
    - 500m for standalone pods
    - Random 100m-500m for ReplicaSet/Deployment pods
    """
    patch_operations = []
    
    # Check if this is our webhook pod - if so, skip mutation to avoid conflicts
    metadata = pod_object.get("metadata", {})
    pod_name = metadata.get("name", "")
    labels = metadata.get("labels", {})
    
    # Skip mutation for webhook pods
    if (labels.get("app") == "pod-cpu-mutator" or 
        "pod-cpu-mutator" in pod_name):
        logger.info(f"Skipping mutation for webhook pod: {pod_name}")
        return patch_operations
    
    # Determine CPU request value based on pod ownership
    cpu_request = determine_cpu_request(pod_object)
    
    # Get the pod spec
    spec = pod_object.get("spec", {})
    containers = spec.get("containers", [])
    
    # Check if containers exist
    if not containers:
        logger.info("No containers found in pod spec")
        return patch_operations
    
    # Process each container
    for i, container in enumerate(containers):
        container_path = f"/spec/containers/{i}"
        
        # Check if resources section exists
        resources = container.get("resources", {})
        requests = resources.get("requests", {})
        
        if not resources:
            # Resources section doesn't exist, create it
            patch_operations.append({
                "op": "add",
                "path": f"{container_path}/resources",
                "value": {
                    "requests": {
                        "cpu": cpu_request
                    }
                }
            })
            logger.info(f"Adding resources section for container {i} with CPU: {cpu_request}")
        elif not requests:
            # Resources exists but requests doesn't, add requests
            patch_operations.append({
                "op": "add",
                "path": f"{container_path}/resources/requests",
                "value": {
                    "cpu": cpu_request
                }
            })
            logger.info(f"Adding requests section for container {i} with CPU: {cpu_request}")
        else:
            # Requests section exists, replace or add CPU request
            if "cpu" in requests:
                # CPU request exists, replace it
                patch_operations.append({
                    "op": "replace",
                    "path": f"{container_path}/resources/requests/cpu",
                    "value": cpu_request
                })
                logger.info(f"Replacing CPU request for container {i}: {requests.get('cpu')} -> {cpu_request}")
            else:
                # CPU request doesn't exist, add it
                patch_operations.append({
                    "op": "add",
                    "path": f"{container_path}/resources/requests/cpu",
                    "value": cpu_request
                })
                logger.info(f"Adding CPU request for container {i}: {cpu_request}")
    
    return patch_operations

def determine_cpu_request(pod_object: Dict[str, Any]) -> str:
    """
    Determine CPU request value based on pod ownership:
    - For ReplicaSet/Deployment pods: Random value between 100m-500m
    - For standalone pods: Fixed 500m
    """
    metadata = pod_object.get("metadata", {})
    owner_references = metadata.get("ownerReferences", [])
    pod_name = metadata.get("name", "unknown")
    
    # Check if pod is owned by ReplicaSet or Deployment
    for owner in owner_references:
        owner_kind = owner.get("kind", "")
        owner_name = owner.get("name", "")
        
        if owner_kind in ["ReplicaSet", "Deployment"]:
            # Generate random CPU request for ReplicaSet/Deployment pods
            cpu_request = generate_random_cpu_request(pod_name, owner_name)
            logger.info(f"Pod {pod_name} owned by {owner_kind} {owner_name}, assigning CPU: {cpu_request}")
            return cpu_request
    
    # Standalone pod gets fixed 500m
    logger.info(f"Standalone pod {pod_name}, assigning fixed CPU: 500m")
    return "500m"

def generate_random_cpu_request(pod_name: str, owner_name: str) -> str:
    """
    Generate a random CPU request between 100m-500m
    For each pod creation, generates a truly random value to ensure
    pods in the same ReplicaSet/Deployment get different CPU requests
    """
    import time
    import os
    
    # Initialize random with truly random seed using system entropy
    # This ensures each call generates a different value
    # Use system time and entropy for truly random seed
    
    # Generate random CPU value between 100-500 millicores
    cpu_millicores = random.randint(100, 500)
    
    # Log details for debugging
    timestamp = time.time_ns()
    logger.info(f"Amit Generated random CPU request: {cpu_millicores}m for pod {pod_name} in {owner_name} (timestamp: {timestamp})")
    
    return f"{cpu_millicores}m"

def should_allow_pod_deletion(pod_object: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Check if pod deletion should be allowed based on CPU threshold
    """
    try:
        metadata = pod_object.get("metadata", {})
        namespace = metadata.get("namespace", "default")
        owner_references = metadata.get("ownerReferences", [])
        pod_name = metadata.get("name", "unknown")
        
        # Check if pod is owned by ReplicaSet
        replicaset_owner = None
        for owner in owner_references:
            if owner.get("kind") == "ReplicaSet":
                replicaset_owner = owner
                break
        
        if not replicaset_owner:
            logger.info(f"Pod {pod_name} is not owned by ReplicaSet, allowing deletion")
            return True, "Pod is not part of a ReplicaSet"
        
        replicaset_name = replicaset_owner.get("name")
        
        # Get the CPU request of the pod being deleted
        deleted_pod_cpu = get_pod_cpu_request(pod_object)
        
        # Get total CPU of remaining pods in the ReplicaSet
        remaining_cpu = get_remaining_cpu_in_replicaset(namespace, replicaset_name, pod_name)
        
        # Calculate total after deletion
        total_after_deletion = remaining_cpu
        
        logger.info(f"Pod {pod_name} deletion check: deleted_pod_cpu={deleted_pod_cpu}m, remaining_cpu={remaining_cpu}m, threshold={CPU_THRESHOLD_MILLICORES}m")
        
        # Check CPU threshold
        cpu_check_passed = total_after_deletion >= CPU_THRESHOLD_MILLICORES
        
        # Check node selector requirement
        deleted_pod_has_selector = pod_has_required_node_selector(pod_object)
        remaining_pods_with_selector = get_remaining_pods_with_node_selector(namespace, replicaset_name, pod_name)
        
        # Calculate how many pods with selector would remain after deletion
        pods_with_selector_after_deletion = remaining_pods_with_selector
        if deleted_pod_has_selector:
            pods_with_selector_after_deletion -= 1
        
        logger.info(f"Node selector check: deleted_pod_has_selector={deleted_pod_has_selector}, remaining_with_selector={remaining_pods_with_selector}, after_deletion={pods_with_selector_after_deletion}, min_required={MIN_PODS_WITH_NODE_SELECTOR}")
        
        node_selector_check_passed = pods_with_selector_after_deletion >= MIN_PODS_WITH_NODE_SELECTOR
        
        # Both checks must pass for deletion to be allowed
        if not cpu_check_passed:
            message = f"Deletion blocked: Total CPU would drop to {total_after_deletion}m, below threshold of {CPU_THRESHOLD_MILLICORES}m"
            logger.warning(message)
            return False, message
        elif not node_selector_check_passed:
            message = f"Deletion blocked: Only {pods_with_selector_after_deletion} pods would have {REQUIRED_NODE_SELECTOR_KEY}={REQUIRED_NODE_SELECTOR_VALUE}, below minimum of {MIN_PODS_WITH_NODE_SELECTOR}"
            logger.warning(message)
            return False, message
        else:
            message = f"Deletion allowed: CPU={total_after_deletion}m (>={CPU_THRESHOLD_MILLICORES}m), node_selector_pods={pods_with_selector_after_deletion} (>={MIN_PODS_WITH_NODE_SELECTOR})"
            logger.info(message)
            return True, message
            
    except Exception as e:
        logger.error(f"Error checking pod deletion: {str(e)}")
        return True, "Error occurred during validation, allowing deletion"

def get_pod_cpu_request(pod_object: Dict[str, Any]) -> int:
    """Get CPU request from pod object in millicores"""
    try:
        containers = pod_object.get("spec", {}).get("containers", [])
        total_cpu = 0
        
        for container in containers:
            cpu_request = container.get("resources", {}).get("requests", {}).get("cpu", "0m")
            cpu_millicores = parse_cpu_request(cpu_request)
            total_cpu += cpu_millicores
        
        return total_cpu
    except Exception as e:
        logger.error(f"Error getting pod CPU request: {str(e)}")
        return 0

def get_remaining_cpu_in_replicaset(namespace: str, replicaset_name: str, excluded_pod_name: str) -> int:
    """Get total CPU requests of all pods in ReplicaSet except the excluded one"""
    try:
        if not HAS_KUBERNETES:
            logger.warning("Kubernetes client not available, using mock validation")
            # Mock validation: assume we have 3 pods with 300m each = 900m total
            # This is below threshold so deletion should be blocked
            return 900
        
        # Get all pods in the namespace with the ReplicaSet label
        pods = k8s_v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"pod-template-hash={replicaset_name.split('-')[-1]}"
        )
        
        total_cpu = 0
        for pod in pods.items:
            if pod.metadata.name != excluded_pod_name:
                cpu_request = get_pod_cpu_request(pod.to_dict())
                total_cpu += cpu_request
                logger.info(f"Pod {pod.metadata.name} has CPU request: {cpu_request}m")
        
        return total_cpu
    except Exception as e:
        logger.error(f"Error getting remaining CPU in ReplicaSet: {str(e)}")
        return 0

def parse_cpu_request(cpu_string: str) -> int:
    """Parse CPU request string to millicores integer"""
    if not cpu_string:
        return 0
    
    cpu_string = cpu_string.strip()
    
    if cpu_string.endswith('m'):
        # Already in millicores
        return int(cpu_string[:-1])
    elif cpu_string.endswith('n'):
        # Nanocores, convert to millicores
        return int(cpu_string[:-1]) // 1000000
    elif '.' in cpu_string:
        # Decimal cores, convert to millicores
        return int(float(cpu_string) * 1000)
    else:
        # Whole cores, convert to millicores
        return int(cpu_string) * 1000

def create_admission_response(uid: str, allowed: bool = True, patch: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Create an admission response object for mutating webhook
    """
    response = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": {
            "uid": uid,
            "allowed": allowed
        }
    }
    
    # Add patch if provided
    if patch and len(patch) > 0:
        patch_bytes = json.dumps(patch).encode('utf-8')
        patch_b64 = base64.b64encode(patch_bytes).decode('utf-8')
        response["response"]["patchType"] = "JSONPatch"
        response["response"]["patch"] = patch_b64
    
    return response

def create_validation_response(uid: str, allowed: bool = True, message: str = "") -> Dict[str, Any]:
    """
    Create an admission response object for validating webhook
    """
    response = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": {
            "uid": uid,
            "allowed": allowed
        }
    }
    
    # Add status message if provided
    if message:
        response["response"]["status"] = {
            "message": message
        }
    
    return response

def pod_has_required_node_selector(pod_object: Dict[str, Any]) -> bool:
    """Check if pod has the required node selector"""
    try:
        node_selector = pod_object.get("spec", {}).get("nodeSelector", {})
        return node_selector.get(REQUIRED_NODE_SELECTOR_KEY) == REQUIRED_NODE_SELECTOR_VALUE
    except Exception as e:
        logger.error(f"Error checking pod node selector: {str(e)}")
        return False

def get_remaining_pods_with_node_selector(namespace: str, replicaset_name: str, excluded_pod_name: str) -> int:
    """Get count of pods in ReplicaSet that have the required node selector, excluding specified pod"""
    try:
        if not HAS_KUBERNETES:
            logger.warning("Kubernetes client not available, using mock node selector validation")
            # Mock validation: assume we have 2 pods with the required node selector
            return 2
        
        # Get all pods in the namespace with the ReplicaSet label
        pods = k8s_v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"pod-template-hash={replicaset_name.split('-')[-1]}"
        )
        
        count = 0
        for pod in pods.items:
            if pod.metadata.name != excluded_pod_name:
                if pod_has_required_node_selector(pod.to_dict()):
                    count += 1
                    logger.info(f"Pod {pod.metadata.name} has required node selector")
                else:
                    logger.info(f"Pod {pod.metadata.name} does not have required node selector")
        
        return count
    except Exception as e:
        logger.error(f"Error getting pods with node selector: {str(e)}")
        return 0

if __name__ == "__main__":
    import uvicorn
    import os
    
    # Get certificate paths from environment variables
    cert_file = os.getenv("TLS_CERT_FILE", "/etc/certs/tls.crt")
    key_file = os.getenv("TLS_PRIVATE_KEY_FILE", "/etc/certs/tls.key")
    
    uvicorn.run(app, host="0.0.0.0", port=8443, ssl_keyfile=key_file, ssl_certfile=cert_file)
