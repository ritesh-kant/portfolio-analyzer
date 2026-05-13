#!/usr/bin/env node

// Load .env variables before starting serverless-offline
require('dotenv').config({ path: '../../.env' });

// Verify MongoDB URI is loaded
if (!process.env.MONGODB_URI) {
  console.error('ERROR: MONGODB_URI not found in environment variables');
  console.error('Make sure .env file exists in project root with MONGODB_URI set');
  process.exit(1);
}

console.log('[Dev] MONGODB_URI loaded: ' + process.env.MONGODB_URI.substring(0, 50) + '...');

// Start serverless offline with inherited env
const { spawn } = require('child_process');
const serverless = spawn('npx', ['serverless', 'offline'], {
  stdio: 'inherit',
  cwd: __dirname,
  env: { ...process.env }, // Pass all env vars including the loaded ones
});

serverless.on('close', (code) => {
  process.exit(code);
});
