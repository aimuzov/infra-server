Production memory policy

Every production Compose service must set positive mem_limit and memswap_limit. The latter is RAM plus swap; equal values disable swap. Missing, zero and unlimited budgets fail validation.

Choose caps from startup, imports and concurrent-work peaks. Leave at least 1 GiB for the host on the current 4 GiB server. Include workers, buffers and native allocations; keep the Node heap below the container cap.

Commit budgets to Compose: docker update is temporary. Existing projects need measured caps before their deployment checks become strict. Do not disable the OOM killer to hide a leak.

Validate the merged production configuration with docker compose config --format json piped to python3 /srv/infra-server/deploy/check-memory-limits.py.

Track RSS, heap, swap activity and external HTTPS checks. Bound diagnostic retention and exclude message contents. Sentry exceptions alone do not cover a process stalled by memory pressure.

New sites use aimuzov/infra-site-template; MCP projects use aimuzov/infra-mcp-template. Their Node heaps start at half the container RAM cap. Caddy and VPN are Go services, so a Node heap cap does not apply to them.
