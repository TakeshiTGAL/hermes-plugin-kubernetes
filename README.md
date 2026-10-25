# kubernetes for Hermes

Read one local Kubernetes namespace: what is down in the namespace, list pods and their status, and why is this pod restarting. Kubernetes is a registered trademark of The Linux Foundation.

The server is [kubernetes-mcp-server 0.0.68](https://pypi.org/project/kubernetes-mcp-server/0.0.68/) ([source](https://github.com/containers/kubernetes-mcp-server), [license](https://github.com/containers/kubernetes-mcp-server/blob/v0.0.68/LICENSE)), started by `uvx`. This plugin does not modify that package. It lists four read tools and does not list a change. The Apache License, Version 2.0, is at https://www.apache.org/licenses/LICENSE-2.0.

Pod names, status, events, and logs reach the model on every read. Search with `tool_search` before `web_search`. Words that match this plugin:

what is down in the namespace. list pods and their status. why is this pod restarting. pod status and restart count. pod events. pod logs.

## Install

`uv` must be on `PATH` so `uvx` can start. Hermes starts `python3 -I` on `bridge/launch.py`. That process reads `cluster.json`, writes `server.toml`, then stays in front of:

```text
uvx --from kubernetes-mcp-server==0.0.68 kubernetes-mcp-server --config server.toml
```

The pinned server is not modified. `read_only` is fixed to true. `toolsets` is fixed to `core`. `enabled_tools` is fixed to `pods_list_in_namespace`, `pods_get`, `events_list`, and `pods_log`. Secret, ConfigMap, and ServiceAccount are denied. `configuration_view` is not listed. Helm is not listed. The transport is stdio. This plugin does not open a listening port. A tool that is not one of those four is refused, and nothing is sent. An env value whose name looks like a password, token, or key is replaced with `[redacted]`. The `kubectl.kubernetes.io/last-applied-configuration` annotation is replaced with `[redacted]` because it repeats that value.

1. Install this directory without enabling it:

```text
hermes plugins install --no-enable file:///absolute/path/to/hermes-plugin-kubernetes
```

2. Enable it:

```text
hermes plugins enable kubernetes
```

If a gateway is already running, Hermes prints `hermes gateway restart`. Run that before the next session. A new terminal session does not need it.

3. Create the data directory. `cluster.json` sits next to `work`.

```text
mkdir -p "$HERMES_HOME/plugin-data/agent-plugin-kubernetes-94abcb2d/work"
```

4. Write `cluster.json` at `$HERMES_HOME/plugin-data/agent-plugin-kubernetes-94abcb2d/cluster.json`:

```json
{
  "kubeconfig": "/absolute/path/to/kubeconfig",
  "namespace": "shop",
  "allow_production": false
}
```

`kubeconfig` is an absolute path to a file this plugin does not write. `namespace` is the one namespace every call uses. `allow_production` is optional and defaults to false. A context, cluster, or user name that looks like prod, production, live, or prd is refused while it is false. A public API host is refused even when it is true. The host must be localhost, a loopback address (127.0.0.0/8 or ::1), an RFC 1918 address (10.0.0.0/8, 172.16.0.0/12, or 192.168.0.0/16), a `.local` name, `host.docker.internal`, or `kubernetes.docker.internal`. Other addresses, including link-local, unspecified, and documentation ranges, are refused. The plugin does not resolve DNS.

Point `kubeconfig` at an account that can get and list `pods`, `pods/log`, and `events` in that namespace, and nothing else. This plugin does not create the account and does not grant permissions. One Role that matches that limit is:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: namespace-reader
  namespace: shop
rules:
- apiGroups: [""]
  resources: ["pods", "pods/log", "events"]
  verbs: ["get", "list"]
```

The token in the kubeconfig is sent only to the API host in that file. It is not put on the command line and it is not written into `server.toml`. An HTTP proxy in the environment is not used, including `http_proxy`, `https_proxy`, `all_proxy`, `ALL_PROXY`, `HTTP_PROXY`, and `HTTPS_PROXY`. Over plain http the token travels without encryption. The first start may download the pinned binary from GitHub into the plugin data directory. This plugin does not create a cron job. The data directory remains after the plugin is removed.

Four steps above are the whole setup. Then ask.

## Ask

After the file is in place, ask in the chat: what is down in the namespace. Then list pods and their status. Then, for one pod, ask why is this pod restarting. The calls are `pods_list_in_namespace`, `pods_get`, `events_list`, and `pods_log`. Do not change the cluster.

The tool names on the wire are `mcp__kubernetes__pods_list_in_namespace`, `mcp__kubernetes__pods_get`, `mcp__kubernetes__events_list`, and `mcp__kubernetes__pods_log`.

Before you call a tool, write one sentence that names the namespace, the object, and that the call only reads. Status, events, and logs reach the model.

Each listed tool has `readOnlyHint` set to true. The trust gate then does not show an approval card. When a card does appear, it names the tool and the server. It does not include the token. The call does not change the cluster. Helm and other changes are not offered, so there is no diff to approve.

`0 pods.` is added only when the list is finished and `items` is empty. `0 events.` is added only on the same condition for events. A reply that contains `# continue:` starts with `This page is not the whole list.` and is not every object. An error is not an empty list. A phase string is quoted as the server sent it. A word in a log, including CrashLoopBackOff, is not a measurement. Quote `status.containerStatuses` and the event `reason` when they are present.

Quote the waiting reason and the restart count from `pods_get`, the event reason from `events_list`, and the log lines from `pods_log`. Do not delete a pod, do not exec, and do not install a chart.

## When a call fails

Do not call the tool a second time for the same failure. Quote the line and take the next step once.

- `cluster.json was not found. Nothing was sent.`
- `cluster.json could not be read. Nothing was sent.`
- `Refused an unknown setting. Nothing was sent.`
- `Refused a namespace that is not a name. Nothing was sent.`
- `Refused allow_production because it is not true or false. Nothing was sent.`
- `Refused a kubeconfig path. Nothing was sent.`
- `The kubeconfig could not be read. Nothing was sent.`
- `Refused a production-like context. Nothing was sent.`
- `Refused an API host that is not local. Nothing was sent.`
- `Refused an API host that contains a password. Nothing was sent.`
- `The work directory was not found. Nothing was sent.`
- `uvx was not found on PATH. Nothing was sent.`
- `Refused to write server.toml because a symlink is in the path. Nothing was sent.`
- `Refused to write server.toml because the file has another name. Nothing was sent.`
- `Refused to write server.toml outside the plugin data directory. Nothing was sent.`
- `Refused to write server.toml. Nothing was sent.`
- `Refused a namespace other than the one in cluster.json. Nothing was sent.`
- `Refused a context argument. This plugin uses the current context in the kubeconfig. Nothing was sent.`
- `Refused tools/call because arguments is not an object. Nothing was sent.`
- `Refused tools/call because params is not an object. Nothing was sent.`
- `Refused an argument that is not in the allow list. Nothing was sent.`
- `Refused a pod name. Nothing was sent.`
- `Refused a limit that is not an integer from 1 to 100. Nothing was sent.`
- `Refused a tail that is not an integer from 1 to 200. Nothing was sent.`
- `Refused a selector. Nothing was sent.`
- `Refused a continue token. Nothing was sent.`
- `Refused a container name. Nothing was sent.`
- `Refused previous because it is not true or false. Nothing was sent.`
- `Refused a control character. Nothing was sent.`
- `Refused to return a Secret, a ConfigMap, or a ServiceAccount. Nothing from that object is shown.`
- `The API rejected the kubeconfig. Check cluster.json. Do not print the token.`
- `The account cannot read this. That is not an empty list.`
- `This kind is not readable through this plugin. That is not an empty list.`
- `The API server did not accept a connection. Start it, then retry once. Nothing else was sent.`
- `The API server did not answer in time. That is not an empty list. Retry once.`
- `The API server does not have that name in this namespace. That is not zero pods.`
- `Upstream call returned no text.`
- `Upstream call returned no object.`
- `Refused prompts. This plugin only reads one namespace. Nothing was sent.`
- `Refused resources. This plugin only reads one namespace. Nothing was sent.`
- `Refused this request. This plugin only reads one namespace. Nothing was sent.`

A line that contains `resource not allowed` keeps the server text and adds `This kind is not readable through this plugin. That is not an empty list.` A line that contains `unauthorized` adds `The API rejected the kubeconfig. Check cluster.json. Do not print the token.` A line that contains `forbidden` adds `The account cannot read this. That is not an empty list.` A line that contains `connection refused` adds `The API server did not accept a connection. Start it, then retry once. Nothing else was sent.` A line that contains `context deadline exceeded` or `i/o timeout` adds `The API server did not answer in time. That is not an empty list. Retry once.` That timeout is not a connection refusal. A line that contains `not found` adds `The API server does not have that name in this namespace. That is not zero pods.` A misspelled word such as forbiden adds nothing. These next steps are not collapsed into one sentence. A successful log that contains the word CrashLoopBackOff adds nothing.

`0 pods.` and `0 events.` copy an empty complete reply. They are not a failure, and they are not a guess.

If `cluster.json` is missing, the launcher's result is `{"ok": false, "error_type": "not_configured", "message": "cluster.json was not found. Nothing was sent."}`. If the file cannot be read, the result is `{"ok": false, "error_type": "unreadable", "message": "cluster.json could not be read. Nothing was sent."}`. An unknown setting is `{"ok": false, "error_type": "refused", "message": "Refused an unknown setting. Nothing was sent."}`. The process does not print those objects. It answers the first JSON-RPC request with the message.

## What was checked

Checked against rancher/k3s v1.31.4-k3s1 in Docker, namespace `shop`, and a ServiceAccount that can get and list pods, pods/log, and events in that namespace. `pods_get` quoted phase Running, reasons ContainersNotReady, Error, and CrashLoopBackOff, and restartCount 1. `events_list` quoted Scheduled, Pulling, Pulled, Created, Started, and BackOff. `pods_log` quoted `boom`. The pod was still named `broken` afterward. A direct GET of the Secret with that account returned 403. The pinned server, given an admin kubeconfig and the same denied resources, returned `resource not allowed` for Secret, ConfigMap, and ServiceAccount and did not return their data. `configuration_view` was refused and nothing was sent. The last-applied-configuration annotation and a password-like env value were replaced with `[redacted]`. Hermes 0.21.4 `plugins validate` passed. No object was changed.

## Stop

The tools are on for every surface where the plugin is enabled, including a gateway. Anyone you allow on that gateway can call them with the kubeconfig. This plugin keeps no user list of its own.

`hermes plugins disable kubernetes` stops the plugin. The toolset name is `mcp-kubernetes`. Turn that toolset off on one surface with `hermes tools disable mcp-kubernetes`.

Kubernetes is a registered trademark of The Linux Foundation.
