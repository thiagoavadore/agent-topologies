# maintenance-scheduler

Owner: Team Fleet (fleet@harbourbikes.example)
Runtime: Python 3.11 script run by cron on one VM (`ops-cron-01`).

## What it does
Every night at 02:00 it reads bike mileage and fault reports, plans the next day's repair routes for the mechanics' vans, and writes the plan to a SQLite file that the mechanics' tablet app downloads at 06:00.

## Topology
- Single VM, single crontab entry. There is no second scheduler and nothing alerts if the job does not run; mechanics notice when the tablet shows yesterday's plan.

## Data
`/var/lib/maintenance/plan.sqlite` and `/var/lib/maintenance/history.sqlite` on the VM's local disk. History holds three years of repair records; it is not copied anywhere else.

## Dependencies
```
requests==2.32.3
ortools==9.11.4210
```
