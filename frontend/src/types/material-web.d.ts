import "react";

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      [tag: `md-${string}`]: any;
    }
  }
}
