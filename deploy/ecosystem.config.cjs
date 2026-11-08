// pm2 apps for the Lumawen API on cobalt. Each stage has its own checkout:
//   test -> ~/lumawen-test (port 3041, test.lumawen.com)
//   prod -> ~/lumawen-prod (port 3040, lumawen.com)
// Started by deploy.sh: pm2 startOrReload deploy/ecosystem.config.cjs --only "lumawen-api-test,lumawen-mail-test"
const path = require("path");
const os = require("os");

const stages = { test: 3041, prod: 3040 };

module.exports = {
  apps: Object.entries(stages).flatMap(([stage, port]) => {
    const api = path.join(os.homedir(), `lumawen-${stage}`, "api");
    return [
      {
        name: `lumawen-api-${stage}`,
        cwd: api,
        script: path.join(api, ".venv/bin/uvicorn"),
        args: [
          "lumawen_api.main:app",
          "--host", "127.0.0.1",
          "--port", String(port),
          "--workers", "2",
          // nginx on the same machine passes the visitor's IP in X-Forwarded-For.
          "--proxy-headers",
          "--forwarded-allow-ips", "127.0.0.1",
        ].join(" "),
        interpreter: "none",
        max_memory_restart: "300M",
        time: true,
      },
      {
        name: `lumawen-mail-${stage}`,
        cwd: api,
        script: path.join(api, ".venv/bin/lumawen-admin"),
        args: "send-emails --loop",
        interpreter: "none",
        max_memory_restart: "150M",
        time: true,
      },
    ];
  }),
};
