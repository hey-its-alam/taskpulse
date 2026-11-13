import http from 'k6/http';
import { Counter, Rate } from 'k6/metrics';

const accepted = new Counter('jobs_accepted');
const limited = new Counter('jobs_rate_limited_429');
const rejected = new Counter('jobs_queue_full_503');
const unexpected = new Counter('jobs_unexpected_status');
const innocentOk = new Rate('innocent_user_accepted');

const types = ['generate_report', 'send_email', 'resize_image'];
const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const RATE = parseInt(__ENV.RATE || '20');
const DURATION = __ENV.DURATION || '30s';

// 202, 429 and 503 are expected answers in this test, not failures
http.setResponseCallback(http.expectedStatuses(202, 429, 503));

export const options = {
  scenarios: {
    // one user sending far above the limit (30 requests/minute)
    hammer: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 10,
      maxVUs: 50,
      exec: 'hammer',
    },
    // a different, well-behaved user (20 requests/minute, below the limit)
    innocent: {
      executor: 'constant-arrival-rate',
      rate: 20,
      timeUnit: '1m',
      duration: DURATION,
      preAllocatedVUs: 2,
      maxVUs: 5,
      exec: 'innocent',
    },
  },
  thresholds: {
    jobs_rate_limited_429: ['count>0'],        // the limiter must kick in
    jobs_unexpected_status: ['count==0'],      // no 500s or other surprises
    innocent_user_accepted: ['rate>0.99'],     // other users are not affected
  },
};

function createJob(apiKey) {
  const type = types[Math.floor(Math.random() * types.length)];

  return http.post(
    `${BASE_URL}/jobs`,
    JSON.stringify({ type: type, payload: { source: 'k6' } }),
    {
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': apiKey,
      },
    }
  );
}

// ONE fixed key, hammered above the limit
export function hammer() {
  const res = createJob('k6-rate-limit-key');

  if (res.status === 202) accepted.add(1);
  else if (res.status === 429) limited.add(1);
  else if (res.status === 503) rejected.add(1);
  else unexpected.add(1);
}

// ANOTHER fixed key, behaving normally
export function innocent() {
  const res = createJob('k6-innocent-key');
  innocentOk.add(res.status === 202);
}