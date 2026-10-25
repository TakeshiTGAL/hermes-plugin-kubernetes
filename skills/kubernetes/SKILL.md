---
name: kubernetes
description: "Read one local Kubernetes namespace for what is down, list pods and their status, and why is this pod restarting."
license: MIT
metadata:
  tags: "kubernetes, pods, namespace, events, logs"
---

Before you call any read, write one sentence that names the namespace, the object, and that the call only reads. Do not call the tool until that sentence is in the reply. Status, events, and logs reach the model. Search with tool_search before web_search.

Use this skill to read one namespace. Call the kubernetes tools instead of a cluster terminal. The wire names are `mcp__kubernetes__pods_list_in_namespace`, `mcp__kubernetes__pods_get`, `mcp__kubernetes__events_list`, and `mcp__kubernetes__pods_log`.

## When to use

Use it for these questions:

- what is down in the namespace
- list pods and their status
- why is this pod restarting

`tool_search` matches English words. Put `kubernetes` in the query. For a broken pod search `what is down in the namespace`. For the catalog search `list pods and their status`. For restarts search `why is this pod restarting`. Reply in the language the person used.

## Procedure

1. Call `pods_list_in_namespace` with no namespace argument. The plugin sends the namespace from `cluster.json`. `0 pods.` is added only when the list is finished and items is empty. When the reply starts with `This page is not the whole list.` the page is not every pod. Ask for the next page before you say the namespace has no such pod.
2. For one pod, call `pods_get` with that name. Quote the phase, the waiting reason, and the restart count from the object. Do not decide that a pod is down because a log line contains a word. A phase you have not seen before is quoted as written.
3. Call `events_list` for that namespace. Quote the event reason and message. `0 events.` is added only when the list is finished and items is empty.
4. Call `pods_log` with that name. Quote the lines. A word in the log, including CrashLoopBackOff, is not a measurement.
5. Do not view the kubeconfig, read a Secret, delete, exec, or install a chart. Nothing is sent for those tools. `configuration_view` is not listed. Helm is not listed.

This plugin does not create a cron job by itself.

## When a call fails

Do not call the tool a second time for the same failure, except when the line says `Call again in a minute.` Quote the line and take the next step once.

- A line that contains `resource not allowed` → `This kind is not readable through this plugin. That is not an empty list.`
- A line that contains `unauthorized` → `The API rejected the kubeconfig. Check cluster.json. Do not print the token.`
- A line that contains `forbidden` → `The account cannot read this. That is not an empty list.`
- A line that contains `connection refused` → `The API server did not accept a connection. Start it, then retry once. Nothing else was sent.`
- A line that contains `context deadline exceeded` or `i/o timeout` → `The API server did not answer in time. That is not an empty list. Retry once.`
- A line that contains `not found` → `The API server does not have that name in this namespace. That is not zero pods.`
- A misspelled word such as forbiden, or the word CrashLoopBackOff in a successful log, adds no next step. Quote the server's text and stop.
- A line that starts with `Refused` and contains `Nothing was sent` → this plugin did not send the call. Say that and stop.
- `cluster.json was not found. Nothing was sent.` → write `cluster.json` next to `work` in the plugin data directory. Nothing was sent.
- `Refused this OS or CPU. Nothing was sent.` → this plugin only starts the v0.0.68 GitHub binaries for darwin-arm64, darwin-amd64, linux-amd64, and linux-arm64. Windows is refused.
- `Refused the kubernetes-mcp-server binary because its sha256 did not match. Nothing was sent.` → delete the file under `.kubernetes-mcp-server/bin/0.0.68/` in the plugin data directory. The next start downloads the release again and checks the sha256. Do not retry the tool call until that file is gone.
- `Refused the kubernetes-mcp-server binary because a symlink is in the path. Nothing was sent.` → remove the symlink under `.kubernetes-mcp-server` in the plugin data directory. Do not retry the tool call until it is gone.
- `The kubernetes-mcp-server binary could not be downloaded. Nothing was sent.` → check the network, then start the plugin again. Do not retry the tool call.
- `Refused to write the kubernetes-mcp-server binary. Nothing was sent.` → the plugin data directory could not take the file. Do not retry the tool call.
- `The kubernetes-mcp-server binary could not be read. Nothing was sent.` → the file is there and this process cannot read it. Do not retry the tool call.
- `The kubernetes-mcp-server binary was not found. Nothing was sent.` → the checked file was gone at start. Do not retry the tool call.
- `The kubernetes-mcp-server binary could not be executed. Nothing was sent.` → the file is not executable by this process. Do not retry the tool call.
- `The kubernetes-mcp-server binary could not be started. Nothing was sent.` → the process did not start. Do not retry the tool call.
- `downloading the pinned binary (0 MB of ~90 MB). Nothing was sent. Call again in a minute.` → the binary is still downloading. The number of MB changes. Wait one minute and call the same tool once. Nothing was sent.

`0 pods.` and `0 events.` copy an empty complete reply. They are not a failure, and they are not a guess.

## Limits

Do not read a namespace the person did not name in `cluster.json`. Do not change an object. The token stays in the kubeconfig file and is sent only to the API host in that file. Do not print the token.
