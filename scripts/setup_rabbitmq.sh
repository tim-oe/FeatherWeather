#!/usr/bin/env bash
set -euo pipefail

echo "=== RabbitMQ Setup for FeatherWeather ==="
echo ""

read -rp "RabbitMQ admin username: " adm_name
read -rsp "RabbitMQ admin password: " adm_pwd
echo ""
read -rsp "New password for 'featherweather' user: " fw_pws
echo ""
echo ""

echo "--- Creating vhost 'fw' ---"
rabbitmqctl add_vhost fw

echo "--- Creating user 'featherweather' ---"
rabbitmqctl add_user featherweather "$fw_pws"

echo "--- Setting permissions for 'featherweather' on vhost 'fw' ---"
rabbitmqctl set_permissions -p fw featherweather ".*" ".*" ".*"

echo "--- Declaring queue 'data' ---"
rabbitmqadmin -u "$adm_name" -p "$adm_pwd" -V fw declare queue name=data durable=true

echo "--- Binding queue 'data' to amq.topic ---"
rabbitmqadmin -u "$adm_name" -p "$adm_pwd" -V fw declare binding \
    source=amq.topic destination=data routing_key=data

echo "--- Setting topic permissions for 'featherweather' ---"
rabbitmqctl set_topic_permissions -p fw featherweather amq.topic ".*" ".*"

echo ""
echo "=== Setup complete ==="
