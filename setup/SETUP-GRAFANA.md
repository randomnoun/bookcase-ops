# SETUP-GRAFANA.md

Grafana charts the metrics stored in prometheus.

## Logging in

* Connect to https://grafana.dev.randomnoun/
* The initial username/password is `admin` / `abc123` 

## Setting a datasource

* From the left-hand side menu, select Connections -> Data sources
* Add a prometheus datasource, with the URL of `http://prometheus-prometheus.prometheus.svc.cluster.local:9090`
* Click the 'Explore' button at the bottom to check it connects OK and that metrics are appearing

## Dashboards

You'll find that a lot of dashboards that purport to work with nginx or k8s don't work, or at least, don't work with this particular configuration.

The ones you want are the 'dotdc' dashboards here: https://grafana.com/grafana/dashboards/?plcmt=top-nav&cta=downloads&search=dotdc


