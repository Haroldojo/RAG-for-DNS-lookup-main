# Nameserver troubleshooting

When a domain does not resolve after a migration, compare the nameservers set at the registrar
with the nameservers assigned by the hosting provider. A nameserver change must be made at the
registrar. Verify that the new provider has an active DNS zone before changing delegation.
Propagation can take up to 24–48 hours, though cached records normally update according to TTL.
