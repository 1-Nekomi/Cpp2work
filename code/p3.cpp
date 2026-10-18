//求存款本息和
#include<iostream>
using namespace std;
const double RATE=0.0155;
int main()
{
	cout<<"2026111308陈依琳"<<endl;
    double money;          //存放本金
	double moneySum;       //存放本息和
	cout<<"请输入存款本金（元）：";
	cin>>money;
	moneySum=money*(1+3*RATE);     //计算本息和
	cout<<"三年后存款的本息和为：";
    cout<<fixed;cout.precision(2);cout<<moneySum<<"元"<<endl;
    return 0;
}