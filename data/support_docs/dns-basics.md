# DNS basics

DNS translates a domain name such as example.com into the IP address used by a browser.
Changes to DNS records can take time to become visible because resolvers cache answers. This
delay is controlled by the record TTL. Check the authoritative zone and wait for the TTL before
assuming a change failed.
