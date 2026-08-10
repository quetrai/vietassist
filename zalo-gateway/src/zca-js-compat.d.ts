declare module "zca-js" {
  export type Credentials = {cookie: unknown; imei: string; userAgent: string; language?: string};
  export enum ThreadType {User = 0, Group = 1}
  export type LoginQREvent = {type: string; data?: any};
  export class Zalo {
    constructor(options?: Record<string, unknown>);
    login(credentials: Credentials): Promise<any>;
    loginQR(options: unknown, callback: (event: LoginQREvent) => void): Promise<any>;
  }
}
