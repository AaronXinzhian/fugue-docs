const express = require('express');
const { listUsers, findById, deleteUser } = require('../models/user');

const router = express.Router();

router.get('/', (req, res) => {
  res.json(listUsers().map(({ passwordHash, ...u }) => u));
});

router.get('/:id', (req, res) => {
  const user = findById(req.params.id);
  if (!user) return res.status(404).json({ error: 'not found' });
  const { passwordHash, ...safe } = user;
  res.json(safe);
});

router.delete('/:id', (req, res) => {
  if (!deleteUser(req.params.id)) {
    return res.status(404).json({ error: 'not found' });
  }
  res.status(204).end();
});

module.exports = router;
