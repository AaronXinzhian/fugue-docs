const crypto = require('crypto');

function hashPassword(plain) {
  const salt = crypto.randomBytes(8).toString('hex');
  const digest = crypto.createHash('sha256').update(salt + plain).digest('hex');
  return `${salt}:${digest}`;
}

function verifyPassword(plain, stored) {
  const [salt, digest] = String(stored).split(':');
  const check = crypto.createHash('sha256').update(salt + plain).digest('hex');
  return check === digest;
}

module.exports = { hashPassword, verifyPassword };
