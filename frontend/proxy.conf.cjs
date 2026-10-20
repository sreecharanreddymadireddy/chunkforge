/** Same-origin browser requests; only the dev server contacts FastAPI directly. */
module.exports = {
  '/api': {
    target: 'http://127.0.0.1:8000',
    secure: false,
    changeOrigin: true,
    pathRewrite: { '^/api': '' },
  },
};
