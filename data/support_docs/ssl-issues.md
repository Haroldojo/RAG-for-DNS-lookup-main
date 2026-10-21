# SSL certificate issues

An SSL warning commonly appears when the certificate has expired, the domain is not included in
the certificate, or the DNS record points to a different server. Confirm the certificate expiry
date and common name or SAN entries. During certificate issuance, keep the validation DNS record
or validation file available until the provider confirms issuance. After installation, make sure
the web server serves the correct certificate for the requested hostname.
