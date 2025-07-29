# Makefile for Pod CPU Mutator

.PHONY: help build deploy test clean logs status

# Default target
help:
	@echo "Pod CPU Request Mutating Admission Controller"
	@echo ""
	@echo "Available targets:"
	@echo "  build    - Build the Docker image"
	@echo "  deploy   - Deploy to minikube (includes build and cert generation)"
	@echo "  test     - Run tests to validate the webhook"
	@echo "  logs     - Show webhook logs"
	@echo "  status   - Show deployment status"
	@echo "  clean    - Remove all resources"
	@echo "  certs    - Generate TLS certificates"
	@echo "  help     - Show this help message"

# Build Docker image
build:
	@echo "🔨 Building Docker image..."
	eval $$(minikube docker-env) && docker build -f Dockerfile.custom -t pod-cpu-mutator:latest .

# Generate certificates
certs:
	@echo "🔐 Generating TLS certificates..."
	./generate-certs.sh

# Deploy to minikube
deploy:
	@echo "🚀 Deploying to minikube..."
	./deploy.sh

# Run tests
test:
	@echo "🧪 Testing webhook..."
	kubectl apply -f test-deployment.yaml
	@echo "Check CPU requests with:"
	@echo "kubectl get pods -l app=test-deployment -o jsonpath='{range .items[*]}{.metadata.name}{\": \"}{.spec.containers[0].resources.requests.cpu}{\"\\n\"}{end}'"
	@echo ""
	@echo "Test validation webhook (deletion protection):"
	@echo "kubectl scale deployment test-deployment --replicas=1"
	@echo "kubectl delete pod -l app=test-deployment  # Should be blocked if total CPU < 2000m"

# Show logs
logs:
	@echo "📋 Showing webhook logs..."
	kubectl logs -l app=pod-cpu-mutator -f

# Show status
status:
	@echo "📊 Deployment Status:"
	@echo ""
	@echo "Pods:"
	kubectl get pods -l app=pod-cpu-mutator
	@echo ""
	@echo "Service:"
	kubectl get service pod-cpu-mutator-service
	@echo ""
	@echo "Webhook:"
	kubectl get mutatingadmissionwebhook pod-cpu-mutator-webhook
	@echo ""
	@echo "Secret:"
	kubectl get secret pod-cpu-mutator-certs

# Clean up all resources
clean:
	@echo "🧹 Cleaning up resources..."
	kubectl delete mutatingadmissionwebhook pod-cpu-mutator-webhook 2>/dev/null || true
	kubectl delete -f deployment.yaml 2>/dev/null || true
	kubectl delete -f service.yaml 2>/dev/null || true
	kubectl delete secret pod-cpu-mutator-certs 2>/dev/null || true
	kubectl delete pod test-pod test-pod-no-cpu 2>/dev/null || true
	rm -f mutating-webhook-with-ca.yaml
	@echo "✅ Cleanup complete!"

# Quick development cycle
dev: clean build deploy test

# Show webhook configuration
webhook-config:
	kubectl get mutatingadmissionwebhook pod-cpu-mutator-webhook -o yaml
