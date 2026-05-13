#!/usr/bin/env node

require('dotenv').config({ path: '../../.env' });

const { spawn } = require('child_process');
const serverless = spawn('npx', ['serverless', 'offline'], {
  stdio: 'inherit',
  cwd: __dirname,
  env: { ...process.env },
});

serverless.on('close', (code) => {
  process.exit(code);
});
