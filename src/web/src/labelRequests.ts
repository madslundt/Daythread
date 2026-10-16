/** A response belongs to one entity/filter/revision snapshot and one reload epoch. */
export class LabelRequestEpoch {
  private current = 0;
  replace() {
    return ++this.current;
  }
  capture() {
    return this.current;
  }
  accepts(token: number) {
    return this.current === token;
  }
}
