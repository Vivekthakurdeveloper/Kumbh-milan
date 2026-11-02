import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 5,
  duration: "30s",
  thresholds: {
    http_req_failed: ["rate<0.05"],
  },
};

const base = __ENV.BASE_URL;
const token = __ENV.TOKEN;

export default function () {
  const headers = { Authorization: `Bearer ${token}` };
  const ready = http.get(`${base}/ready`);
  check(ready, { "ready is 200 or 503": (r) => r.status === 200 || r.status === 503 });
  const summary = http.get(`${base}/api/v1/dashboard/summary`, { headers });
  check(summary, { "summary is not 500": (r) => r.status !== 500 });
  sleep(1);
}
