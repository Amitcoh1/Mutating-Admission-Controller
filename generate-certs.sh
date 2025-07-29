#!/bin/bash

# Script to generate TLS certificates for the mutating admission webhook

set -e

SERVICE_NAME="pod-cpu-mutator-service"
NAMESPACE="default"
SECRET_NAME="pod-cpu-mutator-certs"

echo "🔐 Generating TLS certificates for mutating webhook..."

# Create a temporary directory for certificates
TEMP_DIR=$(mktemp -d)
cd $TEMP_DIR

# Generate CA private key
openssl genrsa -out ca.key 2048

# Generate CA certificate
openssl req -new -x509 -key ca.key -sha256 -subj "/C=US/ST=CA/O=MyOrg/CN=MyCA" -days 3650 -out ca.crt

# Generate server private key
openssl genrsa -out tls.key 2048

# Create certificate signing request
openssl req -new -key tls.key -out server.csr -config <(
cat <<EOF
[req]
default_bits = 2048
prompt = no
distinguished_name = dn
req_extensions = v3_req

[dn]
C=US
ST=CA
O=MyOrg
CN=$SERVICE_NAME.$NAMESPACE.svc.cluster.local

[v3_req]
basicConstraints = CA:FALSE
keyUsage = nonRepudiation, digitalSignature, keyEncipherment
subjectAltName = @alt_names

[alt_names]
DNS.1 = $SERVICE_NAME
DNS.2 = $SERVICE_NAME.$NAMESPACE
DNS.3 = $SERVICE_NAME.$NAMESPACE.svc
DNS.4 = $SERVICE_NAME.$NAMESPACE.svc.cluster.local
EOF
)

# Generate server certificate signed by CA
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out tls.crt -days 365 -extensions v3_req -extfile <(
cat <<EOF
[v3_req]
basicConstraints = CA:FALSE
keyUsage = nonRepudiation, digitalSignature, keyEncipherment
subjectAltName = @alt_names

[alt_names]
DNS.1 = $SERVICE_NAME
DNS.2 = $SERVICE_NAME.$NAMESPACE
DNS.3 = $SERVICE_NAME.$NAMESPACE.svc
DNS.4 = $SERVICE_NAME.$NAMESPACE.svc.cluster.local
EOF
)

# Create Kubernetes secret with certificates
kubectl create secret tls $SECRET_NAME --cert=tls.crt --key=tls.key --dry-run=client -o yaml | kubectl apply -f -

# Update the mutating webhook configuration with the CA bundle
CA_BUNDLE=$(base64 < ca.crt | tr -d '\n')

# Create the final webhook configuration with CA bundle
cat > /Users/amitcohen/groundcover-test/mutating-webhook-with-ca.yaml <<EOF
apiVersion: admissionregistration.k8s.io/v1
kind: MutatingWebhookConfiguration
metadata:
  name: pod-cpu-mutator-webhook
webhooks:
- name: pod-cpu-mutator.default.svc
  clientConfig:
    service:
      name: $SERVICE_NAME
      namespace: $NAMESPACE
      path: /mutate
    caBundle: $CA_BUNDLE
  rules:
  - operations: ["CREATE"]
    apiGroups: [""]
    apiVersions: ["v1"]
    resources: ["pods"]
  admissionReviewVersions: ["v1", "v1beta1"]
  sideEffects: None
  failurePolicy: Fail
EOF

# Clean up
cd ..
rm -rf $TEMP_DIR

echo "✅ TLS certificates generated and secret created!"
echo "📄 Updated webhook configuration saved as: mutating-webhook-with-ca.yaml"
