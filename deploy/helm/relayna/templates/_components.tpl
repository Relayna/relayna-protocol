{{- define "relayna.prefix" -}}
{{- .Release.Name | trunc 38 | trimSuffix "-" -}}
{{- end -}}

{{- define "relayna.components" -}}
{{- $name := include "relayna.prefix" . -}}
postgres:
  image: {{ .Values.images.postgres | quote }}
  ports: {db: 5432}
  dataPath: /var/lib/postgresql/data
  initSql: true
  env:
    - {name: POSTGRES_USER, value: relayna}
    - {name: PGDATA, value: /var/lib/postgresql/data/pgdata}
    - name: POSTGRES_PASSWORD
      valueFrom:
        secretKeyRef: {name: {{ .Values.existingSecret | quote }}, key: db-password}
  readinessProbe:
    exec: {command: [pg_isready, -h, 127.0.0.1, -U, relayna, -d, relayna_gateway]}
redis:
  image: {{ .Values.images.redis | quote }}
  ports: {redis: 6379}
  readinessProbe:
    exec: {command: [redis-cli, ping]}
spooltail:
  image: {{ .Values.images.spooltail | quote }}
  ports: {grpc: 7443}
  dataPath: /data
  fsGroup: 10001
  env:
    - {name: SPOOLTAIL_GRPC_LISTEN, value: '0.0.0.0:7443'}
    - {name: SPOOLTAIL_GRPC_TLS, value: 'off'}
    - {name: SPOOLTAIL_LISTEN, value: '0.0.0.0:7474'}
    - {name: SPOOLTAIL_DATA, value: /data}
  readinessProbe:
    tcpSocket: {port: grpc}
sdk:
  image: {{ .Values.images.sdk | quote }}
  ports: {http: 8080}
  env:
    - {name: SPOOLTAIL_TARGET, value: '{{ $name }}-spooltail:7443'}
    - {name: SPOOLTAIL_BROKER_VERSION, value: {{ .Values.broker.version | quote }}}
    - {name: RELAYNA_SERVICE, value: reference}
    - {name: RELAYNA_TASK_TOPIC, value: reference.tasks}
    - {name: RELAYNA_STATUS_TOPIC, value: reference.status}
    - {name: RELAYNA_WORKER_GROUP, value: reference.workers}
    {{- with .Values.broker.cancelTopic }}
    - {name: RELAYNA_CANCEL_TOPIC, value: {{ . | quote }}}
    {{- end }}
  readinessProbe:
    httpGet: {path: /relayna/capabilities, port: http}
gateway:
  image: {{ .Values.images.gateway | quote }}
  ports: {proxy: 8080, control: 8081}
  env:
    - name: DB_PASSWORD
      valueFrom:
        secretKeyRef: {name: {{ .Values.existingSecret | quote }}, key: db-password}
    - {name: DATABASE_URL, value: 'postgres://relayna:$(DB_PASSWORD)@{{ $name }}-postgres:5432/relayna_gateway'}
    - {name: REDIS_URL, value: 'redis://{{ $name }}-redis:6379/0'}
    - {name: LITELLM_BASE_URL, value: 'http://127.0.0.1:9'}
    - {name: LITELLM_SERVICE_KEY, value: local-reference-unused}
    - {name: ENTRA_AUTH_ENABLED, value: 'false'}
    - name: GATEWAY_ADMIN_TOKEN
      valueFrom:
        secretKeyRef: {name: {{ .Values.existingSecret | quote }}, key: gateway-admin-token}
  readinessProbe:
    httpGet: {path: /admin-ui/readyz, port: control}
{{- if .Values.studio.enabled }}
studio-backend:
  image: {{ .Values.images.studioBackend | quote }}
  ports: {http: 8000}
  migrate: true
  env:
    - name: DB_PASSWORD
      valueFrom:
        secretKeyRef: {name: {{ .Values.existingSecret | quote }}, key: db-password}
    - {name: RELAYNA_STUDIO_DATABASE_URL, value: 'postgresql+asyncpg://relayna:$(DB_PASSWORD)@{{ $name }}-postgres:5432/relayna_studio'}
    - {name: RELAYNA_STUDIO_REDIS_URL, value: 'redis://{{ $name }}-redis:6379/1'}
    - {name: RELAYNA_STUDIO_AUTH_MODE, value: operator}
    - {name: RELAYNA_STUDIO_SESSION_COOKIE_SECURE, value: 'false'}
    - name: RELAYNA_STUDIO_OPERATOR_TOKEN
      valueFrom:
        secretKeyRef: {name: {{ .Values.existingSecret | quote }}, key: studio-operator-token}
    - {name: RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_HOSTS, value: '{{ $name }}-sdk,{{ $name }}-gateway'}
    - {name: RELAYNA_STUDIO_GATEWAY_URL, value: 'http://{{ $name }}-gateway:8081'}
    - {name: RELAYNA_STUDIO_GATEWAY_SERVICE_MAP, value: '{"reference":"reference"}'}
    - name: RELAYNA_STUDIO_GATEWAY_USAGE_TOKEN
      valueFrom:
        secretKeyRef: {name: {{ .Values.existingSecret | quote }}, key: studio-gateway-usage-token, optional: true}
  readinessProbe:
    httpGet: {path: /readyz, port: http}
studio:
  image: {{ .Values.images.studio | quote }}
  ports: {http: 80}
  env:
    - {name: STUDIO_BACKEND_UPSTREAM, value: '{{ $name }}-studio-backend:8000'}
  readinessProbe:
    httpGet: {path: /, port: http}
{{- end }}
{{- end -}}
