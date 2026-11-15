"use strict";
(() => {
  // frontend/devices.ts
  var $ = (selector) => document.querySelector(selector);
  function responseError(payload, status) {
    if (payload && typeof payload === "object" && "error" in payload && typeof payload.error === "string") {
      return payload.error;
    }
    return `Request failed (${status}).`;
  }
  function renderDevices(devices) {
    const list = $("#deviceList");
    const status = $("#deviceListStatus");
    if (!list) return;
    list.replaceChildren();
    if (status)
      status.textContent = devices.length === 0 ? "No paired devices." : `${devices.length} paired device${devices.length === 1 ? "" : "s"}.`;
    for (const device of devices) {
      const item = document.createElement("li");
      item.className = "alert-item";
      const title = document.createElement("strong");
      title.textContent = device.name && device.name.length > 0 ? device.name : "Unnamed device";
      const body = document.createElement("p");
      const seen = device.last_seen ? `Last check-in ${device.last_seen}.` : "No check-in has been recorded.";
      const presence = device.revoked ? "Revoked. The agent token no longer works." : device.stale ? "No recent check-in." : "Connected.";
      body.textContent = `${seen} ${presence}`;
      item.append(title, body);
      if (!device.revoked) {
        const actions = document.createElement("div");
        actions.className = "alert-actions";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "button secondary-button";
        button.textContent = "Revoke";
        button.addEventListener("click", () => {
          button.disabled = true;
          void revokeDevice(device.id);
        });
        actions.append(button);
        item.append(actions);
      }
      list.append(item);
    }
  }
  async function revokeDevice(id) {
    const status = $("#deviceListStatus");
    try {
      const response = await fetch(`/api/devices/${encodeURIComponent(id)}/revoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      });
      if (!response.ok) throw new Error(responseError(await response.json().catch(() => null), response.status));
      await refreshDevices();
    } catch (error) {
      if (status) {
        status.textContent = error instanceof TypeError ? "Could not revoke that device. The server did not respond." : error instanceof Error ? error.message : "Could not revoke that device.";
      }
    }
  }
  async function refreshDevices() {
    const status = $("#deviceListStatus");
    let response;
    try {
      response = await fetch("/api/devices");
    } catch {
      if (status) status.textContent = "Could not load devices. The server did not respond.";
      return;
    }
    if (response.status === 404) return;
    if (!response.ok) {
      if (status) status.textContent = `Could not load devices (${response.status}).`;
      return;
    }
    renderDevices((await response.json()).devices ?? []);
  }
  void refreshDevices();
})();
