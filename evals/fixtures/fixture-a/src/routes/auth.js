const express = require('express');
const { findByEmail, createUser } = require('../models/user');
const { hashPassword, verifyPassword } = require('../utils/hash');

const router = express.Router();

router.post('/register', (req, res) => {
  const { email, password, name } = req.body;
  if (!email || !password) {
    return res.status(400).json({ error: 'email and password required' });
  }
  if (findByEmail(email)) {
    return res.status(409).json({ error: 'email already registered' });
  }
  const user = createUser({ email, name, passwordHash: hashPassword(password) });
  res.status(201).json({ id: user.id, email: user.email });
});

router.post('/login', (req, res) => {
  const { email, password } = req.body;
  const user = findByEmail(email);
  if (!user || !verifyPassword(password, user.passwordHash)) {
    return res.status(401).json({ error: 'invalid credentials' });
  }
  res.json({ token: `fake-token-${user.id}`, id: user.id });
});

module.exports = router;
