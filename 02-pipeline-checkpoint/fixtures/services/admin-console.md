# admin-console

Owner: Team Ops Tools (opstools@harbourbikes.example)
Runtime: Ruby on Rails, 2 replicas across two zones.

## What it does
Internal back-office for support staff: refund bookings, block customers, move bikes between zones.

## Networking
```hcl
resource "aws_security_group_rule" "admin_console_ingress" {
  type        = "ingress"
  from_port   = 443
  to_port     = 443
  protocol    = "tcp"
  cidr_blocks = ["0.0.0.0/0"]
}
```
The console is served on `admin.harbourbikes.example`. Staff log in with a shared username and password kept in the team's password manager; there is no SSO or VPN in front of it yet.

## Dependencies
```
rails 7.2.2
pg 1.5.9
```

## Data
Reads and writes through the other services' internal APIs; no database of its own.
