import http from 'k6/http';
import { Counter } from 'k6/metrics';

// Sends a mix of good and bad traffic, and counts what came back, so every kind of
// failure can be matched with what Grafana, Loki and Tempo show.
//
//   valid   : normal jobs, a new X-API-Key each time (never rate limited)  -> 202
//   bad     : requests with an unknown job type                            -> 4xx (validation)
//   hammer  : ONE fixed key sent far above the limit (off by default)      -> 429
//
// Run:  k6 run -e JOB_RATE=2 -e BAD_RATE=1 -e HAMMER_RATE=0 -e DURATION=2m loadtest/failure_mix.js

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const DURATION = __ENV.DURATION || '2m';
const JOB_RATE = parseInt(__ENV.JOB_RATE || '2');
const BAD_RATE = parseInt(__ENV.BAD_RATE || '1');
const HAMMER_RATE = parseInt(__ENV.HAMMER_RATE || '0');

const accepted = new Counter('resp_202_accepted');
const invalid = new Counter('resp_422_invalid');
const limited = new Counter('resp_429_rate_limited');
const queueFull = new Counter('resp_503_queue_full');
const serverError = new Counter('resp_5xx_server_error');
const networkError = new Counter('resp_0_network_error');
const other = new Counter('resp_other');

const types = ['generate_report', 'send_email', 'resize_image'];

// 202, 422, 429 and 503 are answers we expect here, not test failures
http.setResponseCallback(http.expectedStatuses(202, 422, 429, 503));

function scenario(rate, exec) {
  return {
    executor: 'constant-arrival-rate',
    rate: rate,
    timeUnit: '1s',
    duration: DURATION,
    preAllocatedVUs: 10,
    maxVUs: 100,
    exec: exec,
  };
}

const scenarios = { valid: scenario(JOB_RATE, 'validJob') };
if (BAD_RATE > 0) scenarios.bad = scenario(BAD_RATE, 'badRequest');
if (HAMMER_RATE > 0) scenarios.hammer = scenario(HAMMER_RATE, 'hammer');

export const options = { scenarios: scenarios };

function send(body, apiKey, name) {
  const res = http.post(`${BASE_URL}/jobs`, JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
    tags: { name: name },
  });
  const s = res.status;
  if (s === 202) accepted.add(1);
  else if (s === 422) invalid.add(1);
  else if (s === 429) limited.add(1);
  else if (s === 503) queueFull.add(1);
  else if (s === 0) networkError.add(1);
  else if (s >= 500) serverError.add(1);
  else other.add(1);
  return res;
}

export function validJob() {
  const type = types[Math.floor(Math.random() * types.length)];
  send({ type: type, payload: { source: 'k6-mix' } }, 'mix-' + __VU + '-' + __ITER, 'valid');
}

export function badRequest() {
  send({ type: 'not_a_real_type', payload: { source: 'k6-mix' } }, 'bad-' + __VU + '-' + __ITER, 'bad');
}

export function hammer() {
  send({ type: 'send_email', payload: { source: 'k6-mix' } }, 'mix-hammer-key', 'hammer');
}