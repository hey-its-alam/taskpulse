import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const accepted = new Counter('jobs_accepted');
const limited = new Counter('jobs_rate_limited_429');
const rejected = new Counter('jobs_queue_full_503');
const unexpected = new Counter('jobs_unexpected_status');

const types = ['generate_report', 'send_email', 'resize_image'];
const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const PEAK = parseInt(__ENV.PEAK || '100');
const RECOVERY = __ENV.RECOVERY || '5m';

// 202, 429 and 503 are expected answers in this test, not failures
http.setResponseCallback(http.expectedStatuses(202, 429, 503));

export const options = {
  scenarios: {
    spike: {
      executor: 'ramping-arrival-rate',
      startRate: 1,
      timeUnit: '1s',
      preAllocatedVUs: 50,
      maxVUs: 300,
      stages: [
        { target: 1,    duration: '30s' },      // normal traffic
        { target: PEAK, duration: '10s' },      // sudden spike
        { target: PEAK, duration: '1m'  },      // hold the spike
        { target: 1,    duration: '10s' },      // drop back
        { target: 1,    duration: RECOVERY },   // recovery: does the queue drain?
      ],
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<2000'],          // API must stay responsive
    jobs_unexpected_status: ['count==0'],       // no 500s or other surprises
    dropped_iterations: ['count<50'],           // k6 itself must keep up
  },
};

export default function () {
  const type = types[Math.floor(Math.random() * types.length)];

  const res = http.post(
    `${BASE_URL}/jobs`,
    JSON.stringify({ type: type, payload: { source: 'k6-spike' } }),
    {
      headers: {
        'Content-Type': 'application/json',
        // a new key for every request, so the rate limiter does not interfere
        'X-API-Key': 'spike-' + __VU + '-' + __ITER,
      },
    }
  );

  check(res, {
    'status is 202/429/503': (r) => [202, 429, 503].includes(r.status),
  });

  if (res.status === 202) accepted.add(1);
  else if (res.status === 429) limited.add(1);
  else if (res.status === 503) rejected.add(1);
  else unexpected.add(1);
}