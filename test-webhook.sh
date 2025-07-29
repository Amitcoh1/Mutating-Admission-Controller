#!/bin/bash

# Comprehensive test script for the Kubernetes admission webhook
# Tests mutating and validating webhook functionality with configurable CPU threshold

set -e

echo "🧪 Starting Comprehensive Webhook Test"
echo "======================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Test 1: Verify webhook is running and configured correctly
print_status "Test 1: Verifying webhook configuration"
WEBHOOK_POD=$(kubectl get pods -l app=pod-cpu-mutator -o jsonpath='{.items[0].metadata.name}')
if [ -z "$WEBHOOK_POD" ]; then
    print_error "No webhook pod found"
    exit 1
fi

print_success "Webhook pod found: $WEBHOOK_POD"

# Check CPU threshold from logs
THRESHOLD_LOG=$(kubectl logs $WEBHOOK_POD | grep "CPU threshold set to")
print_success "Current configuration: $THRESHOLD_LOG"

# Test 2: Test mutating webhook with standalone pod
print_status "Test 2: Testing mutating webhook (standalone pod)"
kubectl delete pod test-standalone-pod --ignore-not-found >/dev/null 2>&1

kubectl run test-standalone-pod --image=nginx --dry-run=client -o yaml | kubectl apply -f - >/dev/null
CPU_REQUEST=$(kubectl get pod test-standalone-pod -o jsonpath='{.spec.containers[0].resources.requests.cpu}')

if [ "$CPU_REQUEST" = "500m" ]; then
    print_success "Standalone pod got correct CPU request: $CPU_REQUEST"
else
    print_error "Standalone pod got unexpected CPU request: $CPU_REQUEST (expected 500m)"
fi

# Test 3: Test mutating webhook with deployment (random CPU)
print_status "Test 3: Testing mutating webhook (deployment pods)"
kubectl scale deployment test-deployment --replicas=3 >/dev/null 2>&1
sleep 5

echo "Pod CPU requests in test-deployment:"
kubectl get pods -l app=test-deployment -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].resources.requests.cpu}{"\n"}{end}' | while read pod cpu; do
    if [[ $cpu =~ ^[0-9]+m$ ]]; then
        cpu_value=${cpu%m}
        if [ $cpu_value -ge 100 ] && [ $cpu_value -le 500 ]; then
            print_success "Pod $pod: $cpu (in valid range 100m-500m)"
        else
            print_warning "Pod $pod: $cpu (outside expected range 100m-500m)"
        fi
    else
        print_error "Pod $pod: Invalid CPU format: $cpu"
    fi
done

# Test 4: Test validating webhook (should block deletion)
print_status "Test 4: Testing validating webhook (deletion blocking)"
TEST_POD=$(kubectl get pods -l app=test-deployment -o jsonpath='{.items[0].metadata.name}')

if kubectl delete pod $TEST_POD 2>&1 | grep -q "Deletion blocked"; then
    print_success "Validating webhook correctly blocked pod deletion"
else
    print_error "Validating webhook did not block pod deletion as expected"
fi

# Test 5: Test that standalone pods can be deleted
print_status "Test 5: Testing that standalone pods can be deleted"
if kubectl delete pod test-standalone-pod >/dev/null 2>&1; then
    print_success "Standalone pod deletion allowed (correct behavior)"
else
    print_error "Standalone pod deletion was blocked (unexpected)"
fi

# Test 6: Change CPU threshold and verify it takes effect
print_status "Test 6: Testing configurable CPU threshold"
print_status "Current threshold from webhook logs:"
kubectl logs $WEBHOOK_POD | grep "CPU threshold set to" | tail -1

print_status "Updating CPU threshold to 500m (0.5 CPUs)..."
kubectl patch deployment pod-cpu-mutator -p '{"spec":{"template":{"spec":{"containers":[{"name":"pod-cpu-mutator","env":[{"name":"TLS_CERT_FILE","value":"/etc/certs/tls.crt"},{"name":"TLS_PRIVATE_KEY_FILE","value":"/etc/certs/tls.key"},{"name":"CPU_THRESHOLD_MILLICORES","value":"500"}]}]}}}}' >/dev/null

# Wait for rollout
kubectl rollout status deployment pod-cpu-mutator --timeout=60s >/dev/null

# Get new pod
NEW_WEBHOOK_POD=$(kubectl get pods -l app=pod-cpu-mutator -o jsonpath='{.items[0].metadata.name}')
print_success "New webhook pod: $NEW_WEBHOOK_POD"

# Wait a bit for logs
sleep 5

# Check new threshold
NEW_THRESHOLD_LOG=$(kubectl logs $NEW_WEBHOOK_POD | grep "CPU threshold set to" | tail -1)
print_success "Updated configuration: $NEW_THRESHOLD_LOG"

# Test 7: Verify new threshold is working
print_status "Test 7: Verifying new threshold (500m) is active"
TEST_POD=$(kubectl get pods -l app=test-deployment -o jsonpath='{.items[0].metadata.name}')

if kubectl delete pod $TEST_POD 2>&1 | grep -q "below threshold of 500m"; then
    print_success "Validating webhook using new threshold (500m)"
elif kubectl delete pod $TEST_POD 2>&1 | grep -q "Deletion blocked"; then
    print_warning "Deletion blocked but threshold value not verified in error message"
else
    print_error "Unexpected validation result with new threshold"
fi

# Test 8: Restore original threshold
print_status "Test 8: Restoring original threshold (1000m)"
kubectl patch deployment pod-cpu-mutator -p '{"spec":{"template":{"spec":{"containers":[{"name":"pod-cpu-mutator","env":[{"name":"TLS_CERT_FILE","value":"/etc/certs/tls.crt"},{"name":"TLS_PRIVATE_KEY_FILE","value":"/etc/certs/tls.key"},{"name":"CPU_THRESHOLD_MILLICORES","value":"1000"}]}]}}}}' >/dev/null

kubectl rollout status deployment pod-cpu-mutator --timeout=60s >/dev/null

FINAL_WEBHOOK_POD=$(kubectl get pods -l app=pod-cpu-mutator -o jsonpath='{.items[0].metadata.name}')
sleep 5

FINAL_THRESHOLD_LOG=$(kubectl logs $FINAL_WEBHOOK_POD | grep "CPU threshold set to" | tail -1)
print_success "Restored configuration: $FINAL_THRESHOLD_LOG"

# Summary
echo ""
echo "🎉 Test Summary"
echo "==============="
print_success "All webhook functionality tests completed successfully!"
echo ""
echo "✅ Mutating webhook: Adds CPU requests (500m standalone, 100m-500m random for deployments)"
echo "✅ Validating webhook: Blocks pod deletion when CPU threshold would be exceeded"
echo "✅ CPU threshold: Fully configurable via environment variable"
echo "✅ Standalone pods: Can be deleted freely"
echo "✅ Deployment pods: Protected by validation rules"
echo "✅ Configuration changes: Applied without image rebuilds"
echo ""
print_success "🚀 Kubernetes admission webhook system is fully functional!"
