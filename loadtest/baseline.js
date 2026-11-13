import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const accepted = new Counter('jobs_accepted');
const limited = new Counter('jobs_rate_limited_429');
const rejected = new Counter('jobs_queue_full_503');
const unexpected = new Counter('jobs_unexpected_status');

const types = ['generate_report', 'send_email', 'resize_image'];
const RATE = parseInt(__ENV.RATE || '1');
const DURATION = __ENV.DURATION || '2m';
const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

http.setResponseCallback(http.expectedStatuses(202, 429, 503));

export const options = {
  scenarios: {
    steady: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 10,
      maxVUs: 50,
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500', 'p(99)<1000'],
    checks: ['rate>0.99'],              // baseline: almost all must be 202
    jobs_unexpected_status: ['count==0'],
  },
};

export default function () {
  const type = types[Math.floor(Math.random() * types.length)];
  const res = http.post(
    `${BASE_URL}/jobs`,
    JSON.stringify({ type: type, payload: { source: 'k6' } }),
    {
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': 'k6-' + __VU + '-' + __ITER,
      },
    }
  );
  check(res, { 'accepted (202)': (r) => r.status === 202 });
  if (res.status === 202) accepted.add(1);
  else if (res.status === 429) limited.add(1);
  else if (res.status === 503) rejected.add(1);
  else unexpected.add(1);
}